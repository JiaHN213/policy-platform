from core.models import Record
from django.conf import settings
from django.db import models
from policies.models import Policy


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

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"], name="unique_subscription_request"
            )
        ]


class Notification(Record):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    event = models.ForeignKey("policies.PublicationEvent", on_delete=models.PROTECT)
    title = models.CharField(max_length=500)
    reasons = models.JSONField(default=list)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["user", "event"], name="unique_user_notification")
        ]
