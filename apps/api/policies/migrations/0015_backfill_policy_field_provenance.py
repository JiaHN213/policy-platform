from django.db import migrations


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

SOURCE_FIELDS = {"title", "issuer", "document_number", "publication_date", "body"}


def backfill(apps, schema_editor):
    Policy = apps.get_model("policies", "Policy")
    Provenance = apps.get_model("policies", "PolicyFieldProvenance")
    buffer = []
    for policy in Policy.objects.all().iterator(chunk_size=100):
        scope = policy.scope_evidence or {}
        ai = scope.get("ai_review") or {}
        human = ai.get("human_correction") or {}
        obsidian = scope.get("obsidian_correction") or {}
        human_fields = set(human.get("fields") or []) | set(
            obsidian.get("changed_fields") or []
        )
        locked_fields = set(obsidian.get("locked_fields") or []) | human_fields
        actor_id = human.get("actor_id") or obsidian.get("actor_id")
        summary_quote = ""
        if policy.summary_evidence:
            summary_quote = (policy.summary_evidence[0] or {}).get("quote", "")
        for field_name in TRACKED_FIELDS:
            value = getattr(policy, field_name)
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            if field_name in human_fields:
                source_type = "human_correction"
            elif ai and field_name not in SOURCE_FIELDS:
                source_type = "ai_review"
            elif field_name in SOURCE_FIELDS:
                source_type = "source_metadata"
            else:
                source_type = "deterministic_rule"
            quote = (
                policy.validity_evidence
                if field_name in {"validity_status", "validity_evidence"}
                else summary_quote
                if field_name == "summary"
                else ""
            )
            buffer.append(
                Provenance(
                    policy_id=policy.pk,
                    policy_version=policy.version,
                    field_name=field_name,
                    value_snapshot=value,
                    source_type=source_type,
                    evidence_quote=quote,
                    evidence_location={"kind": "body"} if quote else {},
                    model=ai.get("model", "") if source_type == "ai_review" else "",
                    prompt_version=ai.get("prompt_version", "")
                    if source_type == "ai_review"
                    else "",
                    config_version=ai.get("config_version", "")
                    if source_type == "ai_review"
                    else "",
                    locked=field_name in locked_fields,
                    actor_id=actor_id if source_type == "human_correction" else None,
                )
            )
        if len(buffer) >= 1800:
            Provenance.objects.bulk_create(buffer, batch_size=500)
            buffer = []
    if buffer:
        Provenance.objects.bulk_create(buffer, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [("policies", "0014_policy_field_provenance")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
