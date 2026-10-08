from core.models import Record
from django.conf import settings
from django.db import models
from policies.models import Policy


def default_deadline_days():
    return [7, 1]


class Subscription(Record):
    class TargetView(models.TextChoices):
        POLICY = "policy", "政策文件"
        OPPORTUNITY = "opportunity", "政策机会"
        ALL = "all", "政策与机会"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    keywords = models.CharField(max_length=200, blank=True)
    topic = models.CharField(max_length=30, blank=True)
    document_type = models.CharField(
        max_length=30,
        blank=True,
        default="",
        choices=[("", "全部类型")]
        + [choice for choice in Policy.DocumentType.choices if choice[0] != "unclassified"],
    )
    region = models.CharField(max_length=100, blank=True)
    target_view = models.CharField(
        max_length=20, choices=TargetView.choices, default=TargetView.POLICY
    )
    geographic_level = models.CharField(max_length=20, blank=True)
    province = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    business_domain = models.CharField(max_length=80, blank=True)
    direction_tag = models.CharField(max_length=80, blank=True)
    validity_status = models.CharField(max_length=24, blank=True)
    opportunity_category = models.CharField(max_length=24, blank=True)
    opportunity_status = models.CharField(max_length=24, blank=True)
    acquisition_method = models.CharField(max_length=24, blank=True)
    eligible_keywords = models.CharField(max_length=300, blank=True)
    authority_keywords = models.CharField(max_length=300, blank=True)
    has_deadline = models.BooleanField(null=True, blank=True)
    deadline_within_days = models.PositiveSmallIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)
    idempotency_key = models.CharField(max_length=100)
    source_profile = models.ForeignKey("enterprises.EnterpriseProfile", null=True, blank=True, on_delete=models.SET_NULL)
    source_project = models.ForeignKey("enterprises.EnterpriseProject", null=True, blank=True, on_delete=models.CASCADE)
    managed = models.BooleanField(default=False)
    system_paused = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)
    interest_regions = models.JSONField(default=list)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"], name="unique_subscription_request"
            )
        ]


class Notification(Record):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    recommendation = models.ForeignKey("enterprises.PolicyRecommendation", null=True, blank=True, on_delete=models.SET_NULL)
    event = models.ForeignKey("policies.PublicationEvent", null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20, default="update")
    digest_date = models.DateField(null=True, blank=True)
    delivery_key = models.CharField(max_length=120, blank=True)
    title = models.CharField(max_length=500)
    reasons = models.JSONField(default=list)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "event"], name="unique_user_notification"),
            models.UniqueConstraint(fields=["user", "digest_date"], name="unique_user_daily_digest"),
            models.UniqueConstraint(fields=["user", "delivery_key"], condition=~models.Q(delivery_key=""), name="unique_notification_delivery_key"),
        ]


class NotificationPreference(Record):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    update_mode = models.CharField(max_length=12, choices=[("daily", "每日汇总"), ("instant", "即时通知")], default="daily")
    digest_hour = models.PositiveSmallIntegerField(default=9)
    deadline_enabled = models.BooleanField(default=False)
    deadline_days = models.JSONField(default=default_deadline_days)


class PendingDelivery(Record):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    event = models.ForeignKey("policies.PublicationEvent", null=True, blank=True, on_delete=models.PROTECT)
    recommendation = models.ForeignKey("enterprises.PolicyRecommendation", null=True, blank=True, on_delete=models.CASCADE)
    reasons = models.JSONField(default=list)
    notification = models.ForeignKey(Notification, null=True, on_delete=models.PROTECT, related_name="entries")
    handled_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "event"], name="unique_pending_delivery"),
                       models.UniqueConstraint(fields=["user", "recommendation"], name="unique_pending_recommendation")]


class ProfileFollow(Record):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    profile = models.ForeignKey("enterprises.EnterpriseProfile", on_delete=models.CASCADE)
    project = models.ForeignKey("enterprises.EnterpriseProject", null=True, blank=True, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    last_profile_revision = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "profile"], condition=models.Q(project__isnull=True), name="unique_company_follow"),
            models.UniqueConstraint(fields=["user", "project"], condition=models.Q(project__isnull=False), name="unique_project_follow"),
        ]


class SubscriptionChange(Record):
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="changes")
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    reason = models.CharField(max_length=200)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created_at", "-id"]
