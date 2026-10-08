"""User-owned profile subscriptions with protected edits and reversible history."""
from accounts.models import Membership, User
from accounts.services import access_decision
from django.db import transaction
from enterprises.models import EnterpriseProject
from policies.business_scope import configured_domains

from .models import ProfileFollow, Subscription, SubscriptionChange

RULE_FIELDS = ["name", "keywords", "topic", "document_type", "region", "target_view", "geographic_level", "province", "city", "business_domain", "direction_tag", "validity_status", "opportunity_category", "opportunity_status", "acquisition_method", "eligible_keywords", "authority_keywords", "has_deadline", "deadline_within_days", "active", "interest_regions"]


def snapshot(subscription):
    return {key: getattr(subscription, key) for key in RULE_FIELDS}


def record_change(subscription, before, reason, actor=None):
    return SubscriptionChange.objects.create(subscription=subscription, before=before, after=snapshot(subscription), reason=reason, actor=actor)


@transaction.atomic
def sync_follow(follow):
    """Caller holds the owner user's lock; never modify another profile's rules."""
    if not follow.enabled:
        return 0
    profile = type(follow.profile).objects.select_for_update().get(pk=follow.profile_id)
    project = EnterpriseProject.objects.select_for_update().filter(pk=follow.project_id).first() if follow.project_id else None
    if follow.project_id and not project:
        return 0
    scope = {"user": follow.user, "source_profile": profile, "source_project": project}
    data = project.data if project else profile.data
    revision = project.revision if project else profile.revision
    name = project.name if project else profile.organization.name
    if not Membership.objects.filter(user=follow.user, organization=profile.organization, active=True).exists():
        for sub in Subscription.objects.select_for_update().filter(**scope, managed=True, active=True, deleted_at__isnull=True):
            before = snapshot(sub)
            sub.active, sub.system_paused = False, True
            sub.revision += 1
            sub.save()
            record_change(sub, before, "企业访问权限已取消，暂停画像订阅")
        follow.enabled = False
        follow.last_error = "企业访问权限已变化，已停止自动跟随。"
        follow.save()
        return 0
    decision = access_decision(follow.user, "policy_subscription")
    if not decision["allowed"]:
        follow.last_error = "当前账号暂无订阅权限，待恢复后同步。"
        follow.save()
        return 0
    labels = configured_domains()
    domains = {code for code in data.get("business_domains", []) if code in labels}
    regions = data.get("interest_regions", []) or ([data["city"]] if project and data.get("city") else [])
    existing = {s.business_domain: s for s in Subscription.objects.select_for_update().filter(**scope)}
    missing = domains - existing.keys()
    used = Subscription.objects.filter(user=follow.user, deleted_at__isnull=True).count()
    if decision["limit"] is not None and used + len(missing) > decision["limit"]:
        follow.last_error = "订阅额度不足，本次未同步；请整理订阅后重试。"
        follow.save()
        return 0
    changed = 0
    for domain in sorted(missing):
        sub = Subscription.objects.create(**scope, managed=True,
            name=f"{name} · {labels[domain][0]}"[:100], business_domain=domain,
            target_view="all", interest_regions=regions, idempotency_key=f"follow:{project.pk if project else profile.pk}:{domain}"[:100])
        record_change(sub, {}, "开启画像跟随，生成领域订阅", follow.user)
        changed += 1
    for domain, sub in existing.items():
        if not sub.managed or sub.deleted_at:
            continue
        before = snapshot(sub)
        sub.name = f"{name} · {labels.get(domain, [domain])[0]}"[:100]
        sub.interest_regions = regions
        if domain not in domains:
            sub.active, sub.system_paused = False, True
        elif sub.system_paused:
            sub.active, sub.system_paused = True, False
        if before != snapshot(sub):
            sub.revision += 1
            sub.save()
            record_change(sub, before, f"跟随已确认{'项目' if project else '画像'}第 {revision} 版")
            changed += 1
    follow.last_profile_revision, follow.last_error = revision, ""
    follow.save()
    return changed


@transaction.atomic
def configure_follow(user, profile, enabled=None, project=None):
    User.objects.select_for_update(no_key=True).get(pk=user.pk)
    if project and project.profile_id != profile.pk:
        from rest_framework.exceptions import ValidationError
        raise ValidationError("项目不属于当前企业。")
    follow, _ = ProfileFollow.objects.get_or_create(user=user, profile=profile, project=project)
    if enabled is not None:
        follow.enabled = enabled
        follow.save()
    count = sync_follow(follow)
    return {"status": "unavailable" if follow.last_error else "created" if count else "unchanged",
            "created_count": count, "existing_count": 0,
            "message": follow.last_error or ("已开启画像跟随，系统订阅会随确认后的画像更新；手工修改与关闭状态会保留。" if follow.enabled else "已停止跟随画像；现有订阅保留，可在我的订阅关闭。")}


def sync_pending_follows():
    count = 0
    for follow_id, user_id in ProfileFollow.objects.filter(enabled=True).values_list("pk", "user_id"):
        with transaction.atomic():
            User.objects.select_for_update(no_key=True).get(pk=user_id)
            follow = ProfileFollow.objects.select_related("profile__organization", "project", "user").filter(pk=follow_id).first()
            if not follow:
                continue
            if follow.last_profile_revision != (follow.project.revision if follow.project else follow.profile.revision) or follow.last_error or not Membership.objects.filter(user_id=user_id, organization_id=follow.profile.organization_id, active=True).exists():
                count += sync_follow(follow)
    return count
