from django.db import migrations


def retire_ai_relations(apps, schema_editor):
    PolicyRelation = apps.get_model("policies", "PolicyRelation")
    PolicyEnrichment = apps.get_model("policies", "PolicyEnrichment")

    for relation in PolicyRelation.objects.all().iterator():
        discovery = relation.discovery or {}
        if discovery.get("method") != "ai":
            continue
        if relation.verification_status == "rejected" or relation.verified_by_id:
            discovery.update(
                {
                    "method": "human_override",
                    "legacy_method": "ai",
                    "locked": True,
                }
            )
            relation.discovery = discovery
            relation.save(update_fields=["discovery", "updated_at"])
        else:
            relation.delete()

    for enrichment in PolicyEnrichment.objects.all().iterator():
        result = dict(enrichment.result or {})
        if "relations" in result:
            result.pop("relations", None)
            enrichment.result = result
            enrichment.save(update_fields=["result", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("policies", "0011_policysource")]

    operations = [migrations.RunPython(retire_ai_relations, migrations.RunPython.noop)]
