import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Record(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditRecord(Record):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    object_id = models.UUIDField()
    details = models.JSONField(default=dict)


class SystemConfigRelease(Record):
    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        PUBLISHED = "published", "已发布"
        ARCHIVED = "archived", "已归档"

    version = models.CharField(max_length=80, unique=True)
    schema_version = models.CharField(max_length=20, default="1.0")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    checksum = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_config_releases",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="published_config_releases",
    )
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class SystemConfigDocument(Record):
    release = models.ForeignKey(
        SystemConfigRelease, on_delete=models.CASCADE, related_name="documents"
    )
    key = models.CharField(max_length=100)
    content = models.JSONField(default=dict)
    checksum = models.CharField(max_length=64)

    class Meta:
        ordering = ["key", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["release", "key"], name="unique_system_config_document"
            )
        ]


class SystemConfigAudit(Record):
    release = models.ForeignKey(
        SystemConfigRelease, on_delete=models.CASCADE, related_name="audits"
    )
    namespace = models.CharField(max_length=100, blank=True)
    action = models.CharField(max_length=40)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    reason = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class AIModelProfile(Record):
    class Purpose(models.TextChoices):
        REVIEW = "review", "政策自动审核与摘要"
        SEARCH = "search", "自然语言检索与归纳"
        WIKI_SYNTHESIS = "wiki_synthesis", "Wiki 知识页综合"
        WIKI_RELATIONS = "wiki_relations", "政策关系发现"

    purpose = models.CharField(max_length=40, choices=Purpose.choices, unique=True)
    enabled = models.BooleanField(default=True)
    base_url = models.URLField(max_length=500)
    model = models.CharField(max_length=200)
    api_key = models.TextField(blank=True)
    concurrency = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(4)]
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["purpose"]
