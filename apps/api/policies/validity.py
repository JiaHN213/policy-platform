"""Versioned validity changes derived from verified Wiki relations."""
import copy

from core.models import AuditRecord
from django.utils import timezone
from subscriptions.models import Subscription

from .field_provenance import record_field_values
from .models import (
    Evidence,
    Opportunity,
    OpportunityBatch,
    PolicyEnrichment,
    PolicyRelation,
    PublicationEvent,
)
from .taxonomy import ValidityStatus


def save_derived_validity(policy, status, quote, scope):
    """Caller must hold a policy row lock inside a transaction. Body is unchanged."""
    previous_version, previous_status = policy.version, policy.validity_status
    old_stamp = (policy.scope_evidence or {}).get("validity_content_version", {})
    content_version = previous_version
    if old_stamp.get("derived_version") == previous_version and old_stamp.get("content_hash") == policy.content_hash:
        content_version = old_stamp["version"]
    policy.version += 1
    policy.validity_status, policy.validity_evidence = status, quote
    scope["validity_content_version"] = {"version": content_version, "derived_version": policy.version, "content_hash": policy.content_hash}
    policy.scope_evidence = scope
    if policy.extraction_version == previous_version:
        policy.extraction_version = policy.version
    policy.save(update_fields=["version", "validity_status", "validity_evidence", "scope_evidence", "extraction_version", "updated_at"])
    for evidence in Evidence.objects.filter(policy=policy, policy_version=previous_version):
        evidence.pk = None
        evidence.policy_version = policy.version
        evidence.save(force_insert=True)
    for model in (PolicyRelation, Opportunity, OpportunityBatch):
        model.objects.filter(evidence_policy=policy, evidence_version=previous_version).update(evidence_version=policy.version)
    job = PolicyEnrichment.objects.filter(policy=policy, policy_version=previous_version, status="succeeded").first()
    if job:
        PolicyEnrichment.objects.get_or_create(policy=policy, policy_version=policy.version, defaults={
            "status": "succeeded", "model": job.model, "prompt_version": job.prompt_version,
            "result": copy.deepcopy(job.result) | {"carried_from_version": previous_version},
        })
    record_field_values(policy, ["validity_status", "validity_evidence"], source_type="wiki_llm",
                        evidence={"validity_status": quote, "validity_evidence": quote},
                        evidence_location={"validity_status": scope.get("wiki_validity", {})})
    PublicationEvent.objects.get_or_create(policy=policy, policy_version=policy.version, kind="policy.validity_changed.v1", defaults={
        "payload": {"title": f"政策效力变更为{ValidityStatus(status).label}：{policy.title}"[:500],
                    "event_type": "policy.validity_changed", "previous_version": previous_version,
                    "previous_validity_status": previous_status, "validity_status": status,
                    "changed_fields": ["validity_status", "validity_evidence"],
                    "subscription_ids": [str(pk) for pk in Subscription.objects.filter(active=True).values_list("pk", flat=True)]},
    })
    AuditRecord.objects.create(action="policy.validity.derived", object_id=policy.pk,
                               details={"previous_version": previous_version, "version": policy.version,
                                        "previous_status": previous_status, "status": status,
                                        "relation": scope.get("wiki_validity", {}), "at": timezone.now().isoformat()})
