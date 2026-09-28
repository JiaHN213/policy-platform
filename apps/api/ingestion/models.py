from datetime import datetime, timedelta

from core.models import Record
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Source(Record):
    class ScheduleMode(models.TextChoices):
        INTERVAL = "interval", "按间隔检查"
        DAILY = "daily", "每天定时检查"

    name = models.CharField(max_length=200)
    url = models.URLField(max_length=2000, unique=True)
    enabled = models.BooleanField(default=False)
    adapter = models.CharField(max_length=50, default="gov_library_html_v1")
    interval_minutes = models.PositiveIntegerField(default=1440, validators=[MinValueValidator(30)])
    schedule_mode = models.CharField(
        max_length=20, choices=ScheduleMode.choices, default=ScheduleMode.INTERVAL
    )
    daily_check_time = models.TimeField(null=True, blank=True)
    next_check_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    verification_status = models.CharField(max_length=30, default="unverified")
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.name

    def next_scheduled_check(self, now=None, minimum_interval_minutes=0):
        now = now or timezone.now()
        earliest = now + timedelta(minutes=minimum_interval_minutes)
        if self.schedule_mode == self.ScheduleMode.DAILY and self.daily_check_time:
            local_now = timezone.localtime(now)
            candidate = timezone.make_aware(
                datetime.combine(local_now.date(), self.daily_check_time),
                timezone.get_current_timezone(),
            )
            while candidate <= now or candidate < earliest:
                candidate += timedelta(days=1)
            return candidate
        return now + timedelta(minutes=max(minimum_interval_minutes, self.interval_minutes))


class SourceCheckRun(Record):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=30, default="queued")
    finished_at = models.DateTimeField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    discovered = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    progress = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at", "-id"]


class DiscoveredItem(Record):
    source = models.ForeignKey(Source, on_delete=models.CASCADE)
    url = models.URLField(max_length=2000)
    title = models.CharField(max_length=500)
    status = models.CharField(max_length=30, default="discovered")
    metadata = models.JSONField(default=dict)
    policy = models.ForeignKey("policies.Policy", null=True, blank=True, on_delete=models.PROTECT)
    attempts = models.PositiveIntegerField(default=0)
    lease_until = models.DateTimeField(null=True, blank=True)
    retry_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_detail = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "retry_at"])]
        constraints = [
            models.UniqueConstraint(fields=["source", "url"], name="unique_discovered_url")
        ]


class CrawlPage(Record):
    run = models.ForeignKey(SourceCheckRun, on_delete=models.CASCADE, related_name="pages")
    query = models.CharField(max_length=100, default="")
    page = models.PositiveIntegerField()
    total = models.PositiveIntegerField()
    item_count = models.PositiveIntegerField()
    response_hash = models.CharField(max_length=64)
    object_key = models.CharField(max_length=500)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["run", "query", "page"], name="unique_crawl_page")
        ]


class CrawlThrottle(Record):
    """Cross-process request schedule and cooldown for one remote source."""

    scope = models.CharField(max_length=100, unique=True)
    next_request_at = models.DateTimeField(null=True, blank=True)
    blocked_until = models.DateTimeField(null=True, blank=True)
    last_status_code = models.PositiveIntegerField(null=True, blank=True)
    rejection_count = models.PositiveIntegerField(default=0)
