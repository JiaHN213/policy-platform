from django.db import migrations


def restore_published_policies(apps, schema_editor):
    Policy = apps.get_model("policies", "Policy")
    PolicyEnrichment = apps.get_model("policies", "PolicyEnrichment")

    for policy in Policy.objects.filter(status="candidate"):
        scope = dict(policy.scope_evidence or {})
        if not scope.get("manual_review"):
            continue
        job = (
            PolicyEnrichment.objects.filter(policy=policy, status="succeeded")
            .order_by("-updated_at")
            .first()
        )
        if not job or (job.result or {}).get("finalization", {}).get("status") != "published":
            continue
        scope.pop("manual_review", None)
        scope.pop("manual_review_from_job", None)
        policy.scope_evidence = scope
        policy.status = "published"
        policy.published_at = job.updated_at
        policy.save(update_fields=["scope_evidence", "status", "published_at", "updated_at"])
        if (
            not PolicyEnrichment.objects.filter(policy=policy, policy_version=policy.version)
            .exclude(pk=job.pk)
            .exists()
        ):
            job.policy_version = policy.version
            job.save(update_fields=["policy_version", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("policies", "0007_aireviewcontrol")]

    operations = [migrations.RunPython(restore_published_policies, migrations.RunPython.noop)]
