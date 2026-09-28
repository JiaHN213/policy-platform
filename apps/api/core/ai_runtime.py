"""Runtime AI profiles selected by business purpose."""

from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings
from django.db import OperationalError, ProgrammingError


@dataclass(frozen=True)
class AIProfile:
    purpose: str
    base_url: str
    api_key: str
    model: str
    enabled: bool
    concurrency: int = 1

    @property
    def is_local(self):
        return (urlparse(self.base_url).hostname or "").lower() in {
            "localhost",
            "127.0.0.1",
            "::1",
        }

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
        if stored:
            return AIProfile(
                purpose=stored.purpose,
                base_url=stored.base_url,
                api_key=stored.api_key,
                model=stored.model,
                enabled=stored.enabled,
                concurrency=stored.concurrency,
            )
    except (OperationalError, ProgrammingError, RuntimeError):
        pass
    return environment_profile(purpose)


def ensure_ai_profiles():
    """Create purpose rows from the existing environment configuration once."""
    from core.models import AIModelProfile

    defaults = environment_profile("review")
    for purpose, _ in AIModelProfile.Purpose.choices:
        AIModelProfile.objects.get_or_create(
            purpose=purpose,
            defaults={
                "enabled": True,
                "base_url": defaults.base_url,
                "api_key": defaults.api_key,
                "model": defaults.model,
                "concurrency": (
                    settings.AI_REVIEW_CONCURRENCY if purpose == "review" else 1
                ),
            },
        )
