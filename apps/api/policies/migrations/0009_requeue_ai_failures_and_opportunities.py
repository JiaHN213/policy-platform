from django.db import migrations


def requeue_for_resilient_pipeline(apps, schema_editor):
    Opportunity = apps.get_model("policies", "Opportunity")
    PolicyEnrichment = apps.get_model("policies", "PolicyEnrichment")

    PolicyEnrichment.objects.filter(
        status="failed",
        error_code__in=["INVALID_RELATION_EVIDENCE", "MODEL_OR_PROCESSING_ERROR"],
    ).update(
        status="queued",
        attempts=0,
        error_code="",
        retry_at=None,
        lease_until=None,
    )
    classified_policy_ids = Opportunity.objects.values_list("policy_id", flat=True)
    PolicyEnrichment.objects.filter(
        status="succeeded",
        policy__status="published",
        policy__document_type="opportunity",
    ).exclude(policy_id__in=classified_policy_ids).update(
        status="queued",
        attempts=0,
        error_code="",
        retry_at=None,
        lease_until=None,
    )


class Migration(migrations.Migration):
    dependencies = [("policies", "0008_restore_legacy_manual_review")]

    operations = [migrations.RunPython(requeue_for_resilient_pipeline, migrations.RunPython.noop)]
