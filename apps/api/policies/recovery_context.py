"""Per-invocation recovery budget; never a process-wide monkey patch of AI calls."""
from contextvars import ContextVar

current = ContextVar("policy_review_recovery", default=None)


class RecoveryInterrupted(Exception):
    pass
