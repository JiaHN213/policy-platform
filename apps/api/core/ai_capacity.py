"""Cross-worker model endpoint capacity, using PostgreSQL session advisory locks."""
import hashlib
import time
from functools import wraps
from urllib.parse import urlparse

from django.conf import settings
from django.db import connection

from .ai_runtime import get_ai_profile


class ModelCapacityBusy(ValueError):
    pass


def shared_capacity(fn=None, *, purpose="review"):
    if fn is None:
        return lambda target: shared_capacity(target, purpose=purpose)
    @wraps(fn)
    def call(*args, **kwargs):
        profile = get_ai_profile(kwargs.get("purpose", purpose))
        if connection.vendor != "postgresql" or not profile.configured:
            return fn(*args, **kwargs)
        parsed = urlparse(profile.base_url)
        endpoint = (parsed.hostname or "").lower() + ":" + str(parsed.port or (443 if parsed.scheme == "https" else 80))
        # Different purposes on the same endpoint share the review capacity ceiling.
        capacity = max(1, min(4, settings.AI_REVIEW_CONCURRENCY))
        keys = [int.from_bytes(hashlib.sha256(f"policy-ai:{endpoint}:{slot}".encode()).digest()[:8], "big", signed=True) for slot in range(capacity)]
        purpose_keys = [int.from_bytes(hashlib.sha256(f"policy-ai-purpose:{endpoint}:{profile.purpose}:{slot}".encode()).digest()[:8], "big", signed=True) for slot in range(max(1, min(capacity, profile.concurrency)))]
        acquired = None
        acquired_purpose = None
        deadline = time.monotonic() + 15
        try:
            while acquired is None:
                with connection.cursor() as cursor:
                    if acquired_purpose is None:
                        for key in purpose_keys:
                            cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
                            if cursor.fetchone()[0]:
                                acquired_purpose = key
                                break
                    if acquired_purpose is not None:
                        for key in keys:
                            cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
                            if cursor.fetchone()[0]:
                                acquired = key
                                break
                if acquired is None:
                    if time.monotonic() >= deadline:
                        raise ModelCapacityBusy("模型正在处理其他任务，当前进度已保留，请稍后继续。")
                    time.sleep(0.25)
            return fn(*args, **kwargs)
        finally:
            if acquired is not None:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [acquired])
            if acquired_purpose is not None:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [acquired_purpose])
    return call
