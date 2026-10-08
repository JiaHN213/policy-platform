from accounts.models import Organization
from core.models import Record
from django.conf import settings
from django.db import models


class EnterpriseProfile(Record):
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE)
    # Only explicitly confirmed values are used for matching.
    data = models.JSONField(default=dict)
    evidence = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=1)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    refresh_days = models.PositiveSmallIntegerField(default=0)
    next_research_at = models.DateTimeField(null=True, blank=True)
    research_method = models.CharField(max_length=20, default="search")


class EnterpriseProject(Record):
    profile = models.ForeignKey(EnterpriseProfile, on_delete=models.CASCADE, related_name="projects")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    data = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=1)


class ResearchRun(Record):
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    profile = models.ForeignKey(EnterpriseProfile, null=True, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20, default="company")
    inputs = models.JSONField(default=dict)
    uploaded_material = models.BinaryField(null=True, blank=True)
    fingerprint = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=20, default="queued", db_index=True)
    stage = models.CharField(max_length=100, default="等待处理")
    result = models.JSONField(default=dict)
    error = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    lease_token = models.UUIDField(null=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    agent_snapshot = models.JSONField(default=dict)
    runtime_snapshot = models.JSONField(default=dict)
    checkpoint = models.JSONField(default=dict)
    usage = models.JSONField(default=dict)
    retry_at = models.DateTimeField(null=True, blank=True)
    quota_category = models.CharField(max_length=16, default="normal", choices=[("normal", "正常使用"), ("maintenance", "系统维护")])
    maintenance_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["parent", "fingerprint"], condition=models.Q(parent__isnull=False), name="unique_workflow_child")]


class ScopedSettings(Record):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    profile = models.OneToOneField(EnterpriseProfile, null=True, blank=True, on_delete=models.CASCADE)
    overrides = models.JSONField(default=dict)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(user__isnull=False, profile__isnull=True) | models.Q(user__isnull=True, profile__isnull=False)), name="scoped_settings_exactly_one_owner")]


class ResearchSettings(Record):
    key = models.CharField(max_length=30, unique=True, default="default")
    enabled = models.BooleanField(default=False)
    provider = models.CharField(max_length=20, choices=[("tavily", "Tavily"), ("brave", "Brave Search"), ("searxng", "SearXNG（本地部署）")], default="tavily")
    api_key = models.TextField(blank=True)
    searxng_url = models.CharField(max_length=500, default="http://searxng:8080")
    max_sources = models.PositiveSmallIntegerField(default=6)
    daily_limit = models.PositiveSmallIntegerField(default=10)
    agent_enabled = models.BooleanField(default=False)
    agent_all_organizations = models.BooleanField(default=False)
    agent_organizations = models.JSONField(default=list)
    agent_max_reads = models.PositiveSmallIntegerField(default=3)
    agent_max_calls = models.PositiveSmallIntegerField(default=7)
    agent_max_seconds = models.PositiveIntegerField(default=480)
    workflow_max_policies = models.PositiveSmallIntegerField(default=3)
    workflow_max_calls = models.PositiveSmallIntegerField(default=6)
    workflow_max_seconds = models.PositiveIntegerField(default=480)
    workflow_gap_fill = models.BooleanField(default=True)
    workflow_concurrency = models.PositiveSmallIntegerField(default=1)
    workflow_cache_hours = models.PositiveSmallIntegerField(default=24)
    workflow_retry_limit = models.PositiveSmallIntegerField(default=2)
    workflow_daily_calls = models.PositiveIntegerField(default=40)

    @property
    def search_ready(self):
        return bool(self.enabled and (self.searxng_url if self.provider == "searxng" else self.api_key))


class ResearchStep(Record):
    run = models.ForeignKey(ResearchRun, on_delete=models.CASCADE, related_name="steps")
    sequence = models.PositiveIntegerField()
    label = models.CharField(max_length=120)
    tool = models.CharField(max_length=40)
    status = models.CharField(max_length=20, default="running")
    detail = models.CharField(max_length=400, blank=True)
    finished_at = models.DateTimeField(null=True)
    execution_token = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [models.UniqueConstraint(fields=["run", "sequence"], name="unique_research_step")]


class ResearchArtifact(Record):
    run = models.OneToOneField(ResearchRun, on_delete=models.CASCADE, related_name="artifact")
    data = models.JSONField(default=dict)
    # A draft is never an instruction to write an EnterpriseProfile.
    kind = models.CharField(max_length=30, default="company_draft")


class RecommendationSettings(Record):
    key = models.CharField(max_length=30, unique=True, default="default")
    enabled = models.BooleanField(default=True)
    all_organizations = models.BooleanField(default=True)
    organizations = models.JSONField(default=list)
    batch_size = models.PositiveSmallIntegerField(default=30)
    daily_batches = models.PositiveIntegerField(default=200)
    daily_model_calls = models.PositiveIntegerField(default=20)
    model_calls_per_run = models.PositiveSmallIntegerField(default=2)
    aggregation_minutes = models.PositiveSmallIntegerField(default=10)
    retention_days = models.PositiveSmallIntegerField(default=90)


class PolicyWatch(Record):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    profile = models.ForeignKey(EnterpriseProfile, on_delete=models.CASCADE)
    project = models.ForeignKey(EnterpriseProject, null=True, blank=True, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    view = models.CharField(max_length=20, default="opportunities")
    interval_hours = models.PositiveSmallIntegerField(default=24)
    ai_explanations = models.BooleanField(default=False)
    consent_version = models.PositiveIntegerField(default=1)
    consented_at = models.DateTimeField(null=True)
    next_run_at = models.DateTimeField(null=True)
    last_run_at = models.DateTimeField(null=True)
    last_full_scan_at = models.DateTimeField(null=True)
    event_watermark = models.DateTimeField(null=True)
    input_signature = models.CharField(max_length=64, blank=True)
    has_baseline = models.BooleanField(default=False)
    message = models.CharField(max_length=400, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "profile"], condition=models.Q(project__isnull=True), name="unique_company_policy_watch"),
            models.UniqueConstraint(fields=["user", "project"], condition=models.Q(project__isnull=False), name="unique_project_policy_watch"),
        ]


class WatchRun(Record):
    watch = models.ForeignKey(PolicyWatch, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=20, default="queued", db_index=True)
    signature = models.CharField(max_length=64)
    cutoff = models.DateTimeField()
    full_scan = models.BooleanField(default=False)
    notify_changes = models.BooleanField(default=False)
    cursor = models.UUIDField(null=True)
    lease_token = models.UUIDField(null=True)
    lease_until = models.DateTimeField(null=True)
    retry_at = models.DateTimeField(null=True)
    failures = models.PositiveSmallIntegerField(default=0)
    scanned = models.PositiveIntegerField(default=0)
    matched = models.PositiveIntegerField(default=0)
    model_calls = models.PositiveIntegerField(default=0)
    message = models.CharField(max_length=400, blank=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["watch"], condition=models.Q(status__in=["queued", "running"]), name="one_active_watch_run")]


class PolicyRecommendation(Record):
    watch = models.ForeignKey(PolicyWatch, on_delete=models.CASCADE, related_name="recommendations")
    policy = models.ForeignKey("policies.Policy", on_delete=models.CASCADE)
    fingerprint = models.CharField(max_length=64)
    content_fingerprint = models.CharField(max_length=64)
    profile_revision = models.PositiveIntegerField()
    project_revision = models.PositiveIntegerField(null=True)
    policy_version = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    result = models.JSONField(default=dict)
    feedback = models.CharField(max_length=20, blank=True)
    feedback_note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["watch", "policy", "fingerprint"], name="unique_policy_recommendation")]
