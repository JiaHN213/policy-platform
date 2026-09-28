import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta

from accounts.services import access_decision
from core.business_config import get_config
from django.db import transaction
from django.utils import timezone
from policies.business_scope import configured_domains, configured_tags
from policies.models import OpportunityBatch, Policy, PublicationEvent
from policies.taxonomy import OpportunityCategory, OpportunityStatus, ValidityStatus

from .models import Notification, Subscription


@dataclass
class MatchResult:
    matched: bool
    reasons: list[str] = field(default_factory=list)
    opportunity_titles: list[str] = field(default_factory=list)


def _terms(value):
    return [term.casefold() for term in (value or "").split() if term.strip()]


def _contains_all(text, words):
    haystack = text.casefold()
    return all(word in haystack for word in words)


def _config_labels(group):
    config = get_config("system_taxonomies")
    return {
        item["code"]: item["label"]
        for item in config[group]
        if item.get("enabled", True)
    }


def _choice_labels(choices):
    return dict(choices)


def _opportunity_text(opportunity):
    values = [
        opportunity.title,
        opportunity.support_content,
        opportunity.support_method,
        opportunity.eligible_subjects,
        opportunity.eligible_projects,
        opportunity.eligible_products,
        opportunity.requirements,
        opportunity.regions,
    ]
    return json.dumps(values, ensure_ascii=False, default=str)


def _authority_text(opportunity):
    return json.dumps(
        [
            opportunity.competent_authorities,
            opportunity.acceptance_authorities,
            opportunity.recommendation_authorities,
        ],
        ensure_ascii=False,
        default=str,
    )


def match_subscription(subscription, policy, *, specific_batch_id=None, now=None):
    """Match a subscription against the canonical policy and structured opportunities."""
    reasons = []
    now = now or timezone.now()
    if subscription.document_type and subscription.document_type != policy.document_type:
        return MatchResult(False)
    if subscription.document_type:
        reasons.append(f"文件类型：{policy.get_document_type_display()}")
    if subscription.topic and subscription.topic not in (policy.topics or []):
        return MatchResult(False)
    if subscription.topic:
        reasons.append(f"关注主题：{subscription.topic}")
    if subscription.region and subscription.region != policy.region:
        return MatchResult(False)
    if subscription.region:
        reasons.append(f"地区：{subscription.region}")
    if subscription.geographic_level and subscription.geographic_level != policy.geographic_level:
        return MatchResult(False)
    if subscription.geographic_level:
        reasons.append(f"地域层级：{policy.get_geographic_level_display()}")
    if subscription.province and subscription.province != policy.province:
        return MatchResult(False)
    if subscription.province:
        reasons.append(f"省份：{subscription.province}")
    if subscription.city and subscription.city != policy.city:
        return MatchResult(False)
    if subscription.city:
        reasons.append(f"城市：{subscription.city}")
    if subscription.business_domain and subscription.business_domain not in (
        policy.business_domains or []
    ):
        return MatchResult(False)
    if subscription.business_domain:
        reasons.append(
            f"业务领域：{configured_domains().get(subscription.business_domain, (subscription.business_domain,))[0]}"
        )
    if subscription.direction_tag and subscription.direction_tag not in (
        policy.direction_tags or []
    ):
        return MatchResult(False)
    if subscription.direction_tag:
        reasons.append(
            f"方向标签：{configured_tags().get(subscription.direction_tag, (subscription.direction_tag,))[0]}"
        )
    if subscription.validity_status and subscription.validity_status != policy.validity_status:
        return MatchResult(False)
    if subscription.validity_status:
        reasons.append(
            f"政策效力：{_choice_labels(ValidityStatus.choices).get(subscription.validity_status, subscription.validity_status)}"
        )
    policy_text = json.dumps(
        [policy.title, policy.summary, policy.body, policy.structured_keywords],
        ensure_ascii=False,
        default=str,
    )
    keyword_terms = _terms(subscription.keywords)
    if not _contains_all(policy_text, keyword_terms):
        return MatchResult(False)
    if keyword_terms:
        reasons.append(f"关键词：{'、'.join(subscription.keywords.split())}")

    opportunity_filters = any(
        (
            subscription.opportunity_category,
            subscription.opportunity_status,
            subscription.acquisition_method,
            subscription.eligible_keywords,
            subscription.authority_keywords,
            subscription.has_deadline is not None,
            subscription.deadline_within_days is not None,
            specific_batch_id,
        )
    )
    needs_opportunity = (
        subscription.target_view == Subscription.TargetView.OPPORTUNITY
        or opportunity_filters
    )
    if not needs_opportunity:
        return MatchResult(True, reasons or ["符合全部政策条件"])

    category_labels = _choice_labels(OpportunityCategory.choices)
    status_labels = _choice_labels(OpportunityStatus.choices)
    method_labels = _config_labels("acquisition_methods")
    matched = []
    for opportunity in policy.opportunities.all():
        if (
            subscription.opportunity_category
            and opportunity.category != subscription.opportunity_category
        ):
            continue
        if subscription.opportunity_status and opportunity.status != subscription.opportunity_status:
            continue
        if (
            subscription.acquisition_method
            and opportunity.acquisition_method != subscription.acquisition_method
        ):
            continue
        if not _contains_all(_opportunity_text(opportunity), _terms(subscription.eligible_keywords)):
            continue
        if not _contains_all(_authority_text(opportunity), _terms(subscription.authority_keywords)):
            continue
        batches = list(opportunity.batches.all())
        if specific_batch_id:
            batches = [batch for batch in batches if str(batch.id) == str(specific_batch_id)]
            if not batches:
                continue
        deadline_batches = [batch for batch in batches if batch.deadline_at]
        if subscription.has_deadline is True and not deadline_batches:
            continue
        if subscription.has_deadline is False and deadline_batches:
            continue
        if subscription.deadline_within_days is not None:
            limit = now + timedelta(days=subscription.deadline_within_days)
            deadline_batches = [
                batch for batch in deadline_batches if now <= batch.deadline_at <= limit
            ]
            if not deadline_batches:
                continue
        matched.append(opportunity)
    if not matched:
        return MatchResult(False)

    opportunity_reasons = list(reasons)
    if subscription.opportunity_category:
        opportunity_reasons.append(
            f"机会分类：{category_labels.get(subscription.opportunity_category, subscription.opportunity_category)}"
        )
    if subscription.opportunity_status:
        opportunity_reasons.append(
            f"机会状态：{status_labels.get(subscription.opportunity_status, subscription.opportunity_status)}"
        )
    if subscription.acquisition_method:
        opportunity_reasons.append(
            f"获取方式：{method_labels.get(subscription.acquisition_method, subscription.acquisition_method)}"
        )
    if subscription.eligible_keywords:
        opportunity_reasons.append(
            f"适用对象：{'、'.join(subscription.eligible_keywords.split())}"
        )
    if subscription.authority_keywords:
        opportunity_reasons.append(
            f"主管/受理部门：{'、'.join(subscription.authority_keywords.split())}"
        )
    if subscription.has_deadline is True:
        opportunity_reasons.append("有明确截止时间")
    elif subscription.has_deadline is False:
        opportunity_reasons.append("未设置截止时间")
    if subscription.deadline_within_days is not None:
        opportunity_reasons.append(f"将在 {subscription.deadline_within_days} 天内截止")
    return MatchResult(
        True,
        opportunity_reasons or ["包含结构化政策机会"],
        [opportunity.title for opportunity in matched[:5]],
    )


def matches(subscription, policy_or_payload):
    """Compatibility wrapper for older callers and tests."""
    if isinstance(policy_or_payload, Policy):
        return match_subscription(subscription, policy_or_payload).matched
    payload = policy_or_payload
    if subscription.document_type and subscription.document_type != payload.get("document_type"):
        return False
    if subscription.topic and subscription.topic not in payload.get("topics", []):
        return False
    if subscription.region and subscription.region != payload.get("region"):
        return False
    return _contains_all(
        payload.get("title", "") + " " + payload.get("body", ""),
        _terms(subscription.keywords),
    )


@transaction.atomic
def deliver_event(event_id):
    event = PublicationEvent.objects.select_for_update().select_related("policy").get(pk=event_id)
    if event.delivered_at:
        return 0
    if (
        event.policy.status != Policy.Status.PUBLISHED
        or event.policy.source_grade not in Policy.FORMAL_SOURCE_GRADES
    ):
        event.delivered_at = timezone.now()
        event.save(update_fields=["delivered_at"])
        return 0
    subscriptions = (
        Subscription.objects.select_for_update()
        .select_related("user")
        .filter(
            active=True,
            user__is_active=True,
            pk__in=event.payload.get("subscription_ids", []),
        )
    )
    users = {}
    specific_batch_id = event.payload.get("opportunity_batch_id")
    for sub in subscriptions:
        if event.policy.is_demo and not sub.user.is_staff:
            continue
        result = match_subscription(
            sub,
            event.policy,
            specific_batch_id=specific_batch_id,
        )
        if (
            access_decision(sub.user, "policy_subscription")["allowed"]
            and access_decision(sub.user, "policy_detail")["allowed"]
            and result.matched
        ):
            reason = f"订阅“{sub.name}”：{'；'.join(result.reasons)}"
            users.setdefault(sub.user_id, []).append(reason)
    count = 0
    for user_id, reasons in users.items():
        _, created = Notification.objects.get_or_create(
            user_id=user_id,
            event=event,
            defaults={
                "title": event.payload.get("title", event.policy.title),
                "reasons": sorted(set(reasons)),
            },
        )
        count += int(created)
    event.delivered_at = timezone.now()
    event.save(update_fields=["delivered_at"])
    return count


def create_deadline_reminders():
    """Create one idempotent reminder per subscription and opportunity batch."""
    now = timezone.now()
    subscriptions = list(
        Subscription.objects.filter(
            active=True,
            deadline_within_days__isnull=False,
            user__is_active=True,
        ).select_related("user")
    )
    if not subscriptions:
        return 0
    max_days = max(sub.deadline_within_days for sub in subscriptions)
    batches = (
        OpportunityBatch.objects.filter(
            deadline_at__gte=now,
            deadline_at__lte=now + timedelta(days=max_days),
            opportunity__policy__status=Policy.Status.PUBLISHED,
            opportunity__policy__source_grade__in=Policy.FORMAL_SOURCE_GRADES,
        )
        .select_related("opportunity__policy")
        .prefetch_related("opportunity__policy__opportunities__batches")
    )
    created_count = 0
    for batch in batches:
        policy = batch.opportunity.policy
        for subscription in subscriptions:
            if policy.is_demo and not subscription.user.is_staff:
                continue
            result = match_subscription(
                subscription,
                policy,
                specific_batch_id=batch.id,
                now=now,
            )
            if not result.matched:
                continue
            digest = hashlib.sha256(f"{subscription.id}:{batch.id}".encode()).hexdigest()[:24]
            kind = f"opportunity.deadline.{digest}"
            event, created = PublicationEvent.objects.get_or_create(
                policy=policy,
                policy_version=policy.version,
                kind=kind,
                defaults={
                    "payload": {
                        "title": f"截止提醒：{batch.opportunity.title}",
                        "event_type": "opportunity.deadline",
                        "opportunity_batch_id": str(batch.id),
                        "deadline_at": batch.deadline_at.isoformat(),
                        "subscription_ids": [str(subscription.id)],
                    }
                },
            )
            if created:
                created_count += deliver_event(event.id)
    return created_count
