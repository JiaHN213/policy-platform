from core.models import Record
from django.conf import settings
from django.db import models, transaction

from .taxonomy import (
    DocumentRole,
    OpportunityCategory,
    OpportunityLevel,
    OpportunityStatus,
    RelationKind,
    ValidityStatus,
    VerificationStatus,
)


class Policy(Record):
    class SourceGrade(models.TextChoices):
        UNVERIFIED = "unverified", "待核验"
        L1 = "L1", "官方原始"
        L2 = "L2", "官方转载"
        L3 = "L3", "政府官方业务平台"
        L4 = "L4", "非官方线索"

    class GeographicLevel(models.TextChoices):
        UNVERIFIED = "unverified", "待核验"
        NATIONAL = "national", "国家级"
        PROVINCIAL = "provincial", "省级"
        CITY = "city", "地级市级"

    FORMAL_SOURCE_GRADES = ("L1", "L2", "L3")

    class DocumentType(models.TextChoices):
        UNCLASSIFIED = "unclassified", "待分类"
        POLICY = "policy", "政策与制度文件"
        OPPORTUNITY = "opportunity", "政策机会文件"
        RESULT = "result", "政策执行结果"
        INTERPRETATION = "interpretation", "官方解读"
        DRAFT = "draft", "征求意见稿"

    class Status(models.TextChoices):
        CANDIDATE = "candidate", "待审核"
        PUBLISHED = "published", "已发布"
        WITHDRAWN = "withdrawn", "已撤下"

    title = models.CharField(max_length=500)
    document_number = models.CharField(max_length=200, blank=True)
    issuer = models.CharField(max_length=200)
    publication_date = models.DateField()
    region = models.CharField(max_length=100, default="全国")
    geographic_level = models.CharField(
        max_length=20,
        choices=GeographicLevel.choices,
        default=GeographicLevel.UNVERIFIED,
        db_index=True,
    )
    province = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    source_grade = models.CharField(
        max_length=20, choices=SourceGrade.choices, default=SourceGrade.UNVERIFIED, db_index=True
    )
    topics = models.JSONField(default=list)
    industry = models.CharField(max_length=50, blank=True, db_index=True)
    business_domains = models.JSONField(default=list)
    direction_tags = models.JSONField(default=list)
    scope_evidence = models.JSONField(default=dict)
    document_type = models.CharField(
        max_length=30,
        choices=DocumentType.choices,
        default=DocumentType.UNCLASSIFIED,
        db_index=True,
    )
    document_role = models.CharField(
        max_length=32, choices=DocumentRole.choices, default=DocumentRole.OTHER, db_index=True
    )
    opportunity_level = models.CharField(
        max_length=24,
        choices=OpportunityLevel.choices,
        default=OpportunityLevel.NONE,
        db_index=True,
    )
    support_signals = models.JSONField(default=list)
    body = models.TextField()
    validity_status = models.CharField(
        max_length=24, choices=ValidityStatus.choices, default="unverified", db_index=True
    )
    validity_evidence = models.TextField(blank=True)
    summary = models.TextField(blank=True)
    structured_keywords = models.JSONField(default=list)
    extraction_version = models.PositiveIntegerField(default=0)
    summary_method = models.CharField(max_length=20, default="extractive")
    summary_evidence = models.JSONField(default=list)
    source_url = models.URLField(max_length=2000)
    source_key = models.CharField(max_length=64, unique=True)
    content_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CANDIDATE)
    version = models.PositiveIntegerField(default=1)
    published_at = models.DateTimeField(null=True, blank=True)
    is_demo = models.BooleanField(default=False)

    class Meta:
        ordering = ["-publication_date", "-id"]
        indexes = [models.Index(fields=["status", "-publication_date"])]

    def __str__(self):
        return self.title


class PolicySource(Record):
    """One publication location for a canonical policy document."""

    class Role(models.TextChoices):
        PRIMARY = "primary", "主来源"
        ORIGINAL = "original", "官方原始"
        REPOST = "repost", "官方转载"
        ADDITIONAL = "additional", "其他来源"

    policy = models.ForeignKey(Policy, on_delete=models.CASCADE, related_name="sources")
    url = models.URLField(max_length=2000, unique=True)
    resolved_url = models.URLField(max_length=2000, blank=True)
    publisher = models.CharField(max_length=200, blank=True)
    publication_date = models.DateField(null=True, blank=True)
    source_grade = models.CharField(
        max_length=20, choices=Policy.SourceGrade.choices, default=Policy.SourceGrade.UNVERIFIED
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ADDITIONAL)
    is_primary = models.BooleanField(default=False)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    match_method = models.CharField(max_length=40, default="new_policy")
    match_confidence = models.DecimalField(max_digits=4, decimal_places=3, default=1)
    match_evidence = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-is_primary", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["policy"],
                condition=models.Q(is_primary=True),
                name="unique_primary_policy_source",
            )
        ]


class DocumentSnapshot(Record):
    policy = models.ForeignKey(Policy, on_delete=models.PROTECT, related_name="snapshots")
    url = models.URLField(max_length=2000)
    sha256 = models.CharField(max_length=64)
    object_key = models.CharField(max_length=500)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    parse_status = models.CharField(max_length=30, default="pending")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["policy", "sha256"], name="unique_snapshot")]


class Evidence(Record):
    policy = models.ForeignKey(Policy, on_delete=models.CASCADE, related_name="evidence")
    policy_version = models.PositiveIntegerField()
    text = models.TextField()
    location = models.JSONField(default=dict)
    quote_hash = models.CharField(max_length=64)


class PublicationEvent(Record):
    policy = models.ForeignKey(Policy, on_delete=models.PROTECT)
    policy_version = models.PositiveIntegerField()
    kind = models.CharField(max_length=80, default="policy.published.v1")
    payload = models.JSONField()
    delivered_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        # Persist the event and its independent consumers in the same transaction.
        from .event_consumers import seed_consumers

        creating = self._state.adding
        with transaction.atomic():
            super().save(*args, **kwargs)
            if creating:
                seed_consumers(self)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["policy", "policy_version", "kind"], name="unique_publication_event"
            )
        ]


class PublicationConsumption(Record):
    class Consumer(models.TextChoices):
        SEARCH = "search", "搜索同步"
        SUBSCRIPTION = "subscription", "订阅通知"
        WIKI = "wiki", "Wiki 知识构建"
        STATISTICS = "statistics", "统计审计"

    class Status(models.TextChoices):
        PENDING = "pending", "等待处理"
        RUNNING = "running", "正在处理"
        WAITING = "waiting", "等待知识构建完成"
        RETRY = "retry", "等待自动重试"
        FAILED = "failed", "需要处理"
        SUCCEEDED = "succeeded", "已完成"

    event = models.ForeignKey(PublicationEvent, on_delete=models.CASCADE, related_name="consumptions")
    consumer = models.CharField(max_length=20, choices=Consumer.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    failures = models.PositiveIntegerField(default=0)
    retry_at = models.DateTimeField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    last_success_version = models.PositiveIntegerField(null=True, blank=True)
    succeeded_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict)
    wiki_build = models.ForeignKey("knowledge.KnowledgeBuild", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [models.UniqueConstraint(fields=["event", "consumer"], name="unique_event_consumer")]
        indexes = [models.Index(fields=["consumer", "status", "retry_at"], name="event_consumer_due")]


class VerifiedRecord(Record):
    verification_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default="pending"
    )
    evidence_policy = models.ForeignKey(Policy, on_delete=models.PROTECT, related_name="+")
    evidence_version = models.PositiveIntegerField()
    evidence_quote = models.TextField()
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class Opportunity(VerifiedRecord):
    policy = models.ForeignKey(Policy, on_delete=models.PROTECT, related_name="opportunities")
    title = models.CharField(max_length=500)
    opportunity_key = models.CharField(max_length=64, blank=True)
    category = models.CharField(max_length=24, choices=OpportunityCategory.choices, db_index=True)
    status = models.CharField(
        max_length=24, choices=OpportunityStatus.choices, default="unverified"
    )
    acquisition_method = models.CharField(max_length=24, default="OTHER")
    eligible_subjects = models.JSONField(default=list)
    eligible_projects = models.JSONField(default=list)
    eligible_products = models.JSONField(default=list)
    support_content = models.TextField(blank=True)
    support_method = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    amount_unit = models.CharField(max_length=30, blank=True)
    percentage = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    max_amount = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    min_amount = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    calculation_basis = models.TextField(blank=True)
    requirements = models.JSONField(default=list)
    exclusion_conditions = models.JSONField(default=list)
    prerequisites = models.JSONField(default=list)
    regions = models.JSONField(default=list)
    competent_authorities = models.JSONField(default=list)
    acceptance_authorities = models.JSONField(default=list)
    recommendation_authorities = models.JSONField(default=list)
    application_channels = models.JSONField(default=list)
    missing_information = models.JSONField(default=list)
    evidence_details = models.JSONField(default=list)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["policy", "opportunity_key"],
                condition=~models.Q(opportunity_key=""),
                name="unique_policy_opportunity_key",
            )
        ]


class OpportunityBatch(VerifiedRecord):
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT, related_name="batches")
    name = models.CharField(max_length=200)
    status = models.CharField(
        max_length=24, choices=OpportunityStatus.choices, default="unverified"
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["deadline_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["opportunity", "name"], name="unique_opportunity_batch_name"
            ),
            models.CheckConstraint(
                condition=models.Q(starts_at__isnull=True)
                | models.Q(deadline_at__isnull=True)
                | models.Q(deadline_at__gte=models.F("starts_at")),
                name="batch_deadline_after_start",
            ),
        ]


class PolicyRelation(VerifiedRecord):
    from_policy = models.ForeignKey(
        Policy, on_delete=models.PROTECT, related_name="outgoing_relations"
    )
    to_policy = models.ForeignKey(
        Policy, on_delete=models.PROTECT, related_name="incoming_relations"
    )
    kind = models.CharField(max_length=24, choices=RelationKind.choices)
    discovery = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["from_policy", "to_policy", "kind"], name="unique_directed_policy_relation"
            ),
            models.CheckConstraint(
                condition=~models.Q(from_policy=models.F("to_policy")),
                name="no_self_policy_relation",
            ),
        ]


class PolicyEnrichment(Record):
    finished_at = models.DateTimeField(null=True, blank=True)
    recovery_token = models.UUIDField(null=True, blank=True, db_index=True)
    policy = models.ForeignKey(Policy, on_delete=models.CASCADE, related_name="enrichments")
    policy_version = models.PositiveIntegerField()
    status = models.CharField(max_length=20, default="queued", db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True, blank=True)
    retry_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    model = models.CharField(max_length=200, blank=True)
    prompt_version = models.CharField(max_length=30, default="policy-enrichment-v1")
    result = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["policy", "policy_version"], name="unique_policy_enrichment"
            )
        ]


class PolicyFieldProvenance(Record):
    """Append-only origin and lock history for one editable policy field."""

    class SourceType(models.TextChoices):
        SOURCE_METADATA = "source_metadata", "官方来源信息"
        DETERMINISTIC_RULE = "deterministic_rule", "确定性规则"
        AI_REVIEW = "ai_review", "AI审核"
        WIKI_LLM = "wiki_llm", "Wiki LLM"
        HUMAN_CORRECTION = "human_correction", "人工修正"

    policy = models.ForeignKey(
        Policy, on_delete=models.CASCADE, related_name="field_provenance"
    )
    policy_version = models.PositiveIntegerField()
    field_name = models.CharField(max_length=80, db_index=True)
    value_snapshot = models.JSONField(null=True)
    source_type = models.CharField(max_length=30, choices=SourceType.choices, db_index=True)
    evidence_quote = models.TextField(blank=True)
    evidence_location = models.JSONField(default=dict, blank=True)
    model = models.CharField(max_length=200, blank=True)
    prompt_version = models.CharField(max_length=40, blank=True)
    config_version = models.CharField(max_length=80, blank=True)
    locked = models.BooleanField(default=False, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["field_name", "-created_at", "-id"]
        indexes = [
            models.Index(fields=["policy", "field_name", "-created_at"]),
            models.Index(fields=["policy", "locked"]),
        ]


class AIReviewControl(Record):
    singleton_key = models.CharField(max_length=20, unique=True, default="default", editable=False)
    enabled = models.BooleanField(default=False)
    recovery_enabled = models.BooleanField(default=False)
    recovery_daily_limit = models.PositiveIntegerField(default=20)
    recovery_attempt_limit = models.PositiveIntegerField(default=2)
    recovery_cooldown_minutes = models.PositiveIntegerField(default=30)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )


class ReviewRecovery(Record):
    source_job = models.OneToOneField(PolicyEnrichment, on_delete=models.CASCADE, related_name="recovery")
    policy = models.ForeignKey(Policy, on_delete=models.CASCADE, related_name="review_recoveries")
    policy_version = models.PositiveIntegerField()
    status = models.CharField(max_length=20, default="queued", db_index=True)
    category = models.CharField(max_length=30, blank=True)
    stage = models.CharField(max_length=100, default="等待异常处理")
    message = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True, blank=True)
    retry_at = models.DateTimeField(null=True, blank=True)
    automatic = models.BooleanField(default=False)
    result = models.JSONField(default=dict)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created_at", "-id"]


class SearchIndexState(Record):
    class Status(models.TextChoices):
        IDLE = "idle", "空闲"
        SYNCING = "syncing", "同步中"
        FAILED = "failed", "同步失败"

    singleton_key = models.CharField(max_length=20, unique=True, default="default", editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IDLE)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    indexed_count = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
