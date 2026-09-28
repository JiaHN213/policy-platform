from datetime import date, datetime
from decimal import Decimal

from .models import PolicyFieldProvenance


TRACKED_FIELDS = (
    "title",
    "issuer",
    "document_number",
    "publication_date",
    "body",
    "summary",
    "document_type",
    "source_grade",
    "geographic_level",
    "province",
    "city",
    "validity_status",
    "validity_evidence",
    "business_domains",
    "direction_tags",
    "document_role",
    "opportunity_level",
    "support_signals",
)


def json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def latest_field_provenance(policy):
    latest = {}
    for item in policy.field_provenance.order_by("field_name", "-created_at", "-id"):
        latest.setdefault(item.field_name, item)
    return latest


def locked_policy_fields(policy):
    locked = {name for name, item in latest_field_provenance(policy).items() if item.locked}
    legacy = ((policy.scope_evidence or {}).get("obsidian_correction") or {}).get(
        "locked_fields", []
    )
    return locked | set(legacy)


def record_field_values(
    policy,
    field_names,
    *,
    source_type,
    locked=False,
    actor=None,
    evidence=None,
    evidence_location=None,
    model="",
    prompt_version="",
    config_version="",
):
    evidence = evidence or {}
    locations = evidence_location or {}
    records = []
    for field_name in field_names:
        if field_name not in TRACKED_FIELDS:
            continue
        records.append(
            PolicyFieldProvenance(
                policy=policy,
                policy_version=policy.version,
                field_name=field_name,
                value_snapshot=json_value(getattr(policy, field_name)),
                source_type=source_type,
                evidence_quote=evidence.get(field_name, ""),
                evidence_location=locations.get(field_name, {}),
                model=model,
                prompt_version=prompt_version,
                config_version=config_version,
                locked=locked,
                actor=actor,
            )
        )
    if records:
        PolicyFieldProvenance.objects.bulk_create(records)
    return records


def unlock_policy_field(policy, field_name, actor):
    if field_name not in TRACKED_FIELDS:
        raise ValueError("FIELD_NOT_LOCKABLE")
    previous = latest_field_provenance(policy).get(field_name)
    return PolicyFieldProvenance.objects.create(
        policy=policy,
        policy_version=policy.version,
        field_name=field_name,
        value_snapshot=json_value(getattr(policy, field_name)),
        source_type=PolicyFieldProvenance.SourceType.HUMAN_CORRECTION,
        evidence_quote=previous.evidence_quote if previous else "",
        evidence_location=previous.evidence_location if previous else {},
        locked=False,
        actor=actor,
    )
