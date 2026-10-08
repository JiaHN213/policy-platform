"""Runtime AI profiles selected by business purpose."""

from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

from django.conf import settings
from django.db import OperationalError, ProgrammingError

PURPOSE_PARENTS = {"search_summary": "search", "enterprise_match": "enterprise"}


@dataclass(frozen=True)
class AIProfile:
    purpose: str
    base_url: str
    api_key: str
    model: str
    enabled: bool
    concurrency: int = 1
    thinking: bool = False
    context_tokens: int | None = None
    max_output_tokens: int | None = None
    input_price: Decimal | None = None
    output_price: Decimal | None = None
    currency: str = "CNY"

    @property
    def is_local(self):
        parsed = urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        return host in {
            "localhost",
            "127.0.0.1",
            "::1",
        } or (parsed.port == 11434 and host in {
            # Only the Ollama port: other host services may use a different API.
            "host.docker.internal",
            "gateway.docker.internal",
            "ollama",
        })

    @property
    def configured(self):
        return bool(
            self.enabled
            and self.base_url
            and self.model
            and (self.is_local or self.api_key)
        )


def environment_profile(purpose):
    return AIProfile(
        purpose=purpose,
        base_url=settings.AI_BASE_URL,
        api_key=settings.AI_API_KEY,
        model=settings.AI_MODEL,
        enabled=True,
        concurrency=settings.AI_REVIEW_CONCURRENCY if purpose == "review" else 1,
    )


def get_ai_profile(purpose="review"):
    try:
        from core.models import AIModelProfile

        stored = AIModelProfile.objects.filter(purpose=purpose).first()
        if stored is None and purpose in PURPOSE_PARENTS:
            stored = AIModelProfile.objects.filter(purpose=PURPOSE_PARENTS[purpose]).first()
        if stored:
            return AIProfile(
                purpose=purpose,
                base_url=stored.base_url,
                api_key=stored.api_key,
                model=stored.model,
                enabled=stored.enabled,
                concurrency=stored.concurrency,
                thinking=stored.thinking,
                context_tokens=stored.context_tokens,
                max_output_tokens=stored.max_output_tokens,
                input_price=stored.input_price,
                output_price=stored.output_price,
                currency=stored.currency,
            )
    except (OperationalError, ProgrammingError, RuntimeError):
        pass
    return environment_profile(purpose)


def ensure_ai_profiles():
    """Create purpose rows from the existing environment configuration once."""
    from core.models import AIModelProfile

    defaults = environment_profile("review")
    for purpose, _ in AIModelProfile.Purpose.choices:
        inherited = get_ai_profile(PURPOSE_PARENTS[purpose]) if purpose in PURPOSE_PARENTS else defaults
        AIModelProfile.objects.get_or_create(
            purpose=purpose,
            defaults={
                "enabled": inherited.enabled,
                "base_url": inherited.base_url,
                "api_key": inherited.api_key,
                "model": inherited.model,
                "thinking": inherited.thinking,
                "context_tokens": inherited.context_tokens,
                "max_output_tokens": inherited.max_output_tokens,
                "input_price": inherited.input_price,
                "output_price": inherited.output_price,
                "currency": inherited.currency,
                "concurrency": (
                    settings.AI_REVIEW_CONCURRENCY if purpose == "review" else inherited.concurrency if purpose in PURPOSE_PARENTS else 1
                ),
            },
        )


def apply_generation_settings(profile, payload):
    """Apply supported transport settings; never silently truncate source text."""
    if profile.is_local:
        payload["think"] = profile.thinking
        if profile.context_tokens:
            payload.setdefault("options", {})["num_ctx"] = profile.context_tokens
        if profile.max_output_tokens:
            payload.setdefault("options", {})["num_predict"] = min(payload["options"].get("num_predict", profile.max_output_tokens), profile.max_output_tokens)
    elif profile.max_output_tokens:
        payload["max_tokens"] = min(payload.get("max_tokens", profile.max_output_tokens), profile.max_output_tokens)
    return payload


def profile_signature(profile):
    """Private cache identity, including credential rotation, never sent to clients."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps([profile.base_url, profile.api_key, profile.model,
        profile.enabled, profile.thinking, profile.context_tokens, profile.max_output_tokens], sort_keys=True).encode()).hexdigest()
