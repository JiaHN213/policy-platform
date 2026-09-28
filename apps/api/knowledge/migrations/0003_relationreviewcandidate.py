# Generated manually for rejected Wiki LLM relation proposal review.

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("knowledge", "0002_knowledgerelationscan"),
        ("policies", "0013_opportunity_analysis_v11"),
    ]

    operations = [
        migrations.CreateModel(
            name="RelationReviewCandidate",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("proposal_hash", models.CharField(max_length=64, unique=True)),
                ("proposed_kind", models.CharField(max_length=24)),
                ("evidence_quote", models.TextField(blank=True)),
                (
                    "confidence",
                    models.DecimalField(blank=True, decimal_places=4, max_digits=5, null=True),
                ),
                ("model_reason", models.TextField(blank=True)),
                ("rejection_reason", models.TextField()),
                ("source_versions", models.JSONField(default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "待人工复审"),
                            ("approved", "人工确认有关系"),
                            ("rejected", "人工确认无关系"),
                            ("superseded", "已由后续结果替代"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "evidence_policy",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="relation_review_evidence",
                        to="policies.policy",
                    ),
                ),
                (
                    "from_policy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="relation_review_from",
                        to="policies.policy",
                    ),
                ),
                (
                    "relation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="policies.policyrelation",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="accounts.user",
                    ),
                ),
                (
                    "scan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="review_candidates",
                        to="knowledge.knowledgerelationscan",
                    ),
                ),
                (
                    "to_policy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="relation_review_to",
                        to="policies.policy",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["status", "-created_at"],
                        name="knowledge_r_status_f9f8e7_idx",
                    )
                ],
            },
        )
    ]
