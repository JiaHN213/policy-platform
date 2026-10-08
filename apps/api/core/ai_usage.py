"""Measure individual HTTP attempts without persisting prompts or credentials."""
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal

import httpx
from django.db import transaction

logger = logging.getLogger(__name__)
_task_context = ContextVar("ai_task_context", default=None)


@contextmanager
def task_usage(task_id, *, step_id=None, guard=None):
    token = _task_context.set((task_id, step_id, guard))
    try:
        yield
    finally:
        _task_context.reset(token)


@contextmanager
def step_usage(step_id):
    current = _task_context.get()
    token = _task_context.set((current[0], step_id, current[2]) if current else None)
    try:
        yield
    finally:
        _task_context.reset(token)


def token_count(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def request_json(client, profile, endpoint, *, headers, payload):
    context = _task_context.get()
    if context and context[2]:
        context[2]()
    step_id = context[1]() if context and callable(context[1]) else context[1] if context else None
    started = time.monotonic()
    status, inputs, outputs = "failed", None, None
    try:
        response = client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("MODEL_INVALID_OUTPUT")
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        inputs = token_count(body.get("prompt_eval_count") if profile.is_local else usage.get("prompt_tokens"))
        outputs = token_count(body.get("eval_count") if profile.is_local else usage.get("completion_tokens"))
        status = "received"
        return body
    except httpx.TimeoutException:
        status = "timeout"
        raise
    finally:
        cost = None
        if inputs is not None and outputs is not None and profile.input_price is not None and profile.output_price is not None:
            cost = (Decimal(inputs) * profile.input_price + Decimal(outputs) * profile.output_price) / Decimal(1000000)
        try:
            from .models import AICall
            # Savepoint prevents a logging failure from poisoning a caller's transaction.
            with transaction.atomic():
                AICall.objects.create(purpose=profile.purpose, model=profile.model, status=status,
                    task_id=context[0] if context else None, step_id=step_id,
                    duration_ms=max(0, round((time.monotonic()-started)*1000)), input_tokens=inputs,
                    output_tokens=outputs, estimated_cost=cost, currency=profile.currency,
                    input_price=profile.input_price, output_price=profile.output_price)
        except Exception:
            logger.warning("AI usage record unavailable; request outcome was preserved")
