from .models import Entitlement

DEFAULT_CAPABILITIES = {"policy_search", "policy_detail", "policy_subscription"}


def access_decision(user, capability):
    if not user.is_authenticated or not user.is_active:
        return {"allowed": False, "reason_code": "AUTH_REQUIRED"}
    grant = Entitlement.objects.filter(user=user, capability=capability).first()
    allowed = grant.allowed if grant else capability in DEFAULT_CAPABILITIES
    return {
        "allowed": allowed,
        "reason_code": "OK" if allowed else "NO_GRANT",
        "limit": grant.limit if grant else None,
    }
