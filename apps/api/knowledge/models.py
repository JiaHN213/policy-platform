from core.models import Record
from django.db import models


class KnowledgePage(Record):
    class PageType(models.TextChoices):
        POLICY = "policy", "政策知识页"
        CHAIN = "chain", "政策链"
        TOPIC = "topic", "业务专题"
        REGION = "region", "地区专题"

    class Status(models.TextChoices):
        PUBLISHED = "published", "已发布"
        STALE = "stale", "需要更新"
        ARCHIVED = "archived", "已归档"

    key = models.CharField(max_length=300, unique=True)
    slug = models.SlugField(max_length=300, unique=True)
    page_type = models.CharField(max_length=20, choices=PageType.choices, db_index=True)
    title = models.CharField(max_length=500)
    abstract = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PUBLISHED, db_index=True
    )
    current_revision = models.ForeignKey(
        "KnowledgeRevision",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_for_pages",
    )
    source_count = models.PositiveIntegerField(default=0)
    built_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["page_type", "title", "id"]
        indexes = [models.Index(fields=["status", "page_type"])]

    def __str__(self):
        return self.title


class KnowledgeRevision(Record):
    page = models.ForeignKey(KnowledgePage, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    body = models.TextField()
    citations = models.JSONField(default=list)
    source_versions = models.JSONField(default=dict)
    input_hash = models.CharField(max_length=64, db_index=True)
    model = models.CharField(max_length=200, blank=True)
    prompt_version = models.CharField(max_length=40, default="wiki-grounded-v1")

    class Meta:
        ordering = ["-number", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["page", "number"], name="unique_knowledge_page_revision"
            )
        ]


class KnowledgePageSource(Record):
    page = models.ForeignKey(KnowledgePage, on_delete=models.CASCADE, related_name="page_sources")
    policy = models.ForeignKey(
        "policies.Policy", on_delete=models.PROTECT, related_name="knowledge_sources"
    )
    policy_version = models.PositiveIntegerField()
    role = models.CharField(max_length=30, default="source")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["page", "policy", "role"], name="unique_knowledge_page_source"
            )
        ]


class KnowledgeBuild(Record):
    class Status(models.TextChoices):
        QUEUED = "queued", "等待构建"
        RUNNING = "running", "正在构建"
        SUCCEEDED = "succeeded", "构建完成"
        FAILED = "failed", "构建失败"

    kind = models.CharField(max_length=20, default="sync")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    input_hash = models.CharField(max_length=64, blank=True, db_index=True)
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=80, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-created_at", "-id"]


class KnowledgeRelationScan(Record):
    """One version-bound Wiki LLM relation analysis for an anchor policy."""

    class Status(models.TextChoices):
        SUCCEEDED = "succeeded", "分析完成"
        FAILED = "failed", "分析失败"

    anchor_policy = models.ForeignKey(
        "policies.Policy", on_delete=models.CASCADE, related_name="knowledge_relation_scans"
    )
    anchor_version = models.PositiveIntegerField()
    candidate_versions = models.JSONField(default=dict)
    input_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, db_index=True)
    attempts = models.PositiveIntegerField(default=1)
    model = models.CharField(max_length=200, blank=True)
    prompt_version = models.CharField(max_length=40, default="wiki-relations-v1")
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["anchor_policy", "anchor_version", "status"])]


class RelationReviewCandidate(Record):
    """A Wiki LLM proposal rejected by deterministic validation and kept for human review."""

    class Status(models.TextChoices):
        PENDING = "pending", "待人工复审"
        APPROVED = "approved", "人工确认有关系"
        REJECTED = "rejected", "人工确认无关系"
        SUPERSEDED = "superseded", "已由后续结果替代"

    scan = models.ForeignKey(
        KnowledgeRelationScan, on_delete=models.CASCADE, related_name="review_candidates"
    )
    proposal_hash = models.CharField(max_length=64, unique=True)
    from_policy = models.ForeignKey(
        "policies.Policy", on_delete=models.PROTECT, related_name="relation_review_from"
    )
    to_policy = models.ForeignKey(
        "policies.Policy", on_delete=models.PROTECT, related_name="relation_review_to"
    )
    proposed_kind = models.CharField(max_length=24)
    evidence_policy = models.ForeignKey(
        "policies.Policy",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="relation_review_evidence",
    )
    evidence_quote = models.TextField(blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    model_reason = models.TextField(blank=True)
    rejection_reason = models.TextField()
    source_versions = models.JSONField(default=dict)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    reviewed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    relation = models.ForeignKey(
        "policies.PolicyRelation", null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "-created_at"])]


class KnowledgeLintIssue(Record):
    page = models.ForeignKey(KnowledgePage, on_delete=models.CASCADE, related_name="lint_issues")
    revision = models.ForeignKey(
        KnowledgeRevision, on_delete=models.CASCADE, related_name="lint_issues"
    )
    code = models.CharField(max_length=50)
    severity = models.CharField(max_length=20, default="error")
    message = models.CharField(max_length=500)
    citation_index = models.PositiveIntegerField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "id"]
