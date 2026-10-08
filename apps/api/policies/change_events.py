"""Capture verified opportunity window changes, including AI and manual saves."""
import hashlib

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from subscriptions.models import Subscription
from subscriptions.services import match_subscription

from .models import Opportunity, OpportunityBatch, PublicationEvent


@receiver(pre_save, sender=Opportunity)
@receiver(pre_save, sender=OpportunityBatch)
def remember_window(sender, instance, raw=False, **kwargs):
    instance._window_change = None
    if raw or instance._state.adding:
        return
    old = sender.objects.filter(pk=instance.pk).first()
    fields = ["status"] if sender is Opportunity else ["status", "starts_at", "deadline_at"]
    changed = [key for key in fields if old and getattr(old, key) != getattr(instance, key)]
    if not changed or old.verification_status != "verified" or instance.verification_status != "verified":
        return
    policy = old.policy if sender is Opportunity else old.opportunity.policy
    if policy.status != "published" or policy.source_grade not in {"L1", "L2", "L3"} or old.evidence_version != old.evidence_policy.version:
        return
    subscriptions = list(Subscription.objects.filter(active=True, deleted_at__isnull=True))
    matched = {str(sub.pk): sub.revision for sub in subscriptions if match_subscription(sub, policy, specific_batch_id=old.pk if sender is OpportunityBatch else None).matched}
    instance._window_change = {"policy": policy, "fields": changed, "previous_matching": matched,
                               "subscription_ids": [str(sub.pk) for sub in subscriptions]}


@receiver(post_save, sender=Opportunity)
@receiver(post_save, sender=OpportunityBatch)
def publish_window_change(sender, instance, raw=False, **kwargs):
    change = getattr(instance, "_window_change", None)
    if raw or not change:
        return
    policy = change.pop("policy")
    opportunity = instance if sender is Opportunity else instance.opportunity
    details = []
    for key in change["fields"]:
        value = getattr(instance, key)
        details.append(("状态：" + instance.get_status_display()) if key == "status" else ("截止时间" if key == "deadline_at" else "开始时间") + "已调整为" + (value.isoformat() if value else "待核实"))
    digest = hashlib.sha256(f"{instance.pk}:{instance.updated_at.isoformat()}:{details}".encode()).hexdigest()[:32]
    PublicationEvent.objects.get_or_create(policy=policy, policy_version=policy.version, kind="opportunity.changed." + digest,
        defaults={"payload": {"title": ("机会重要变化：" + opportunity.title)[:500], "change_details": details,
                               "changed_fields": change["fields"], "previous_matching": change["previous_matching"],
                               "subscription_ids": change["subscription_ids"], "opportunity_id": str(opportunity.pk),
                               **({"opportunity_batch_id": str(instance.pk)} if sender is OpportunityBatch else {})}})
