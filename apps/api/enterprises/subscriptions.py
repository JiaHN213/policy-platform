"""Create optional personal subscriptions from explicitly confirmed business domains."""
import hashlib

from accounts.services import access_decision
from core.models import AuditRecord
from policies.business_scope import configured_domains
from subscriptions.models import Subscription


def subscribe_to_profile(user, profile):
    """Caller holds the user's row lock inside the profile confirmation transaction."""
    decision = access_decision(user, "policy_subscription")
    result = {"created_count": 0, "existing_count": 0, "status": "unavailable", "message": ""}
    if not decision["allowed"]:
        return result | {"message": "画像已保存，但当前账号暂无订阅权限。可继续查看匹配政策。"}
    labels = configured_domains()
    domains = list(dict.fromkeys(code for code in profile.data.get("business_domains", []) if code in labels))
    if not domains:
        return result | {"message": "画像已保存，尚未确定业务领域，未创建订阅。补充业务领域后可再次开启。"}
    subscriptions = Subscription.objects.filter(user=user)
    pending = []
    paused_or_modified = 0
    for domain in domains:
        key = "enterprise-domain:" + hashlib.sha256(domain.encode()).hexdigest()
        existing = subscriptions.filter(idempotency_key=key).first()
        # A manually created equivalent rule also satisfies the request. Do not widen it.
        baseline = Subscription(business_domain=domain, target_view="all")
        filters = {field.name: getattr(baseline, field.name) for field in Subscription._meta.fields
                   if field.name not in {"id", "created_at", "updated_at", "user", "name", "idempotency_key", "active", "source_profile", "source_project", "managed", "system_paused", "revision", "deleted_at"}}
        equivalent = subscriptions.filter(active=True, deleted_at__isnull=True, **filters).first()
        if equivalent:
            result["existing_count"] += 1
        elif existing:
            result["existing_count"] += 1
            paused_or_modified += 1
        else:
            pending.append((domain, key))
    if decision["limit"] is not None and subscriptions.count() + len(pending) > decision["limit"]:
        return result | {"message": "画像已保存，订阅额度不足，未新增订阅。请在“我的订阅”整理已有规则后重试。"}
    for domain, key in pending:
        item = Subscription.objects.create(user=user, name=f"企业关注 · {labels[domain][0]}"[:100],
                                           business_domain=domain, target_view="all", idempotency_key=key)
        AuditRecord.objects.create(actor=user, action="enterprise.subscription.created", object_id=item.pk,
                                   details={"profile_id": str(profile.pk), "revision": profile.revision,
                                            "business_domain": domain, "consent": "explicit_confirmation"})
    count = len(pending)
    message = f"已按业务领域新增 {count} 条订阅。" if count else "已有相关订阅，未重复创建。"
    if paused_or_modified:
        message += "之前暂停或修改的订阅已保留，请到“我的订阅”查看。"
    message += "后续相关政策更新将通过站内通知提醒，可随时修改或关闭。"
    return result | {"status": "created" if count else "unchanged", "created_count": count, "message": message}
