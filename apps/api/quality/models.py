from core.models import Record
from django.conf import settings
from django.db import models


class EvaluationSample(Record):
    class Kind(models.TextChoices):
        OPPORTUNITY = "opportunity", "政策机会识别"
        RELATION = "relation", "Wiki政策关系"
        KNOWLEDGE = "knowledge", "Wiki知识页证据"

    class Status(models.TextChoices):
        PENDING = "pending", "待人工标注"
        LABELED = "labeled", "已人工标注"
        RETIRED = "retired", "已停用"

    key = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, db_index=True)
    title = models.CharField(max_length=1000)
    policy = models.ForeignKey("policies.Policy", null=True, on_delete=models.SET_NULL)
    related_policy = models.ForeignKey(
        "policies.Policy", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    page = models.ForeignKey("knowledge.KnowledgePage", null=True, on_delete=models.SET_NULL)
    relation_kind = models.CharField(max_length=24, blank=True)
    snapshot = models.JSONField(default=dict)
    origin = models.CharField(max_length=50, default="manual")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    gold = models.JSONField(default=dict)
    label_version = models.PositiveIntegerField(default=0)
    labeled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    labeled_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ["-created_at", "id"]


class EvaluationRun(Record):
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, default="completed")
    protocol_version = models.CharField(max_length=40, default="quality-v1")
    config_version = models.CharField(max_length=100, blank=True)
    metrics = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at", "id"]


class EvaluationResult(Record):
    run = models.ForeignKey(EvaluationRun, on_delete=models.CASCADE, related_name="results")
    sample = models.ForeignKey(EvaluationSample, on_delete=models.PROTECT)
    label_version = models.PositiveIntegerField()
    status = models.CharField(max_length=20)
    reason = models.TextField(blank=True)
    gold = models.JSONField(default=dict)
    prediction = models.JSONField(default=dict)
    correct = models.BooleanField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "sample"], name="unique_quality_run_sample")
        ]
