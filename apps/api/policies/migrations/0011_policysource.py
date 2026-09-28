import uuid

import django.db.models.deletion
from django.db import migrations, models


def backfill_policy_sources(apps, schema_editor):
    Policy = apps.get_model("policies", "Policy")
    PolicySource = apps.get_model("policies", "PolicySource")
    for policy in Policy.objects.iterator():
        PolicySource.objects.get_or_create(
            url=policy.source_url,
            defaults={
                "id": uuid.uuid4(),
                "policy_id": policy.pk,
                "resolved_url": policy.source_url,
                "publisher": policy.issuer,
                "publication_date": policy.publication_date,
                "source_grade": policy.source_grade,
                "role": "primary",
                "is_primary": True,
                "content_hash": policy.content_hash,
                "match_method": "legacy_primary",
                "match_confidence": 1,
                "match_evidence": {"backfilled": True},
            },
        )


class Migration(migrations.Migration):
    dependencies = [("policies", "0010_searchindexstate")]

    operations = [
        migrations.CreateModel(
            name="PolicySource",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("url", models.URLField(max_length=2000, unique=True)),
                ("resolved_url", models.URLField(blank=True, max_length=2000)),
                ("publisher", models.CharField(blank=True, max_length=200)),
                ("publication_date", models.DateField(blank=True, null=True)),
                ("source_grade", models.CharField(choices=[("unverified", "待核验"), ("L1", "官方原始"), ("L2", "官方转载"), ("L3", "政府官方业务平台"), ("L4", "非官方线索")], default="unverified", max_length=20)),
                ("role", models.CharField(choices=[("primary", "主来源"), ("original", "官方原始"), ("repost", "官方转载"), ("additional", "其他来源")], default="additional", max_length=20)),
                ("is_primary", models.BooleanField(default=False)),
                ("content_hash", models.CharField(blank=True, db_index=True, max_length=64)),
                ("match_method", models.CharField(default="new_policy", max_length=40)),
                ("match_confidence", models.DecimalField(decimal_places=3, default=1, max_digits=4)),
                ("match_evidence", models.JSONField(blank=True, default=dict)),
                ("policy", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sources", to="policies.policy")),
            ],
            options={"ordering": ["-is_primary", "created_at", "id"]},
        ),
        migrations.RunPython(backfill_policy_sources, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="policysource",
            constraint=models.UniqueConstraint(condition=models.Q(("is_primary", True)), fields=("policy",), name="unique_primary_policy_source"),
        ),
    ]
