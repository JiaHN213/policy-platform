from datetime import datetime
from urllib.parse import urlparse

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import CrawlThrottle, DiscoveredItem, Source, SourceCheckRun
from .tasks import queue_check


class SourceSerializer(serializers.ModelSerializer):
    collection_type = serializers.ChoiceField(
        choices=[
            ("nanning_v1", "南宁市政策文件库"),
            ("gov_library_html_v1", "中国政府网政策文件库"),
        ],
        source="adapter",
        required=False,
    )
    collection_type_label = serializers.SerializerMethodField()
    cooldown_until = serializers.SerializerMethodField()
    cooldown_reason = serializers.SerializerMethodField()
    crawl_state = serializers.SerializerMethodField()

    class Meta:
        model = Source
        fields = [
            "id",
            "name",
            "url",
            "collection_type",
            "collection_type_label",
            "enabled",
            "interval_minutes",
            "schedule_mode",
            "daily_check_time",
            "verification_status",
            "last_success_at",
            "next_check_at",
            "cooldown_until",
            "cooldown_reason",
            "crawl_state",
            "notes",
        ]
        read_only_fields = [
            "id",
            "collection_type_label",
            "verification_status",
            "last_success_at",
            "next_check_at",
            "cooldown_until",
            "cooldown_reason",
            "crawl_state",
        ]

    def get_collection_type_label(self, obj):
        return {
            "nanning_v1": "南宁市政策文件库",
            "gov_library_html_v1": "中国政府网政策文件库",
        }.get(obj.adapter, "已登记的政策文件库")

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        adapter = attrs.get("adapter", getattr(instance, "adapter", ""))
        url = attrs.get("url", getattr(instance, "url", ""))
        host = (urlparse(url).hostname or "").lower()
        if adapter == "nanning_v1" and not (
            host == "nanning.gov.cn" or host.endswith(".nanning.gov.cn")
        ):
            raise serializers.ValidationError(
                {"url": "南宁市采集规则只能用于南宁市政府网站地址。"}
            )
        if adapter == "gov_library_html_v1" and host not in {
            "www.gov.cn",
            "sousuo.www.gov.cn",
        }:
            raise serializers.ValidationError(
                {"url": "中国政府网采集规则只能用于中国政府网政策文件库地址。"}
            )
        schedule_mode = attrs.get(
            "schedule_mode", getattr(instance, "schedule_mode", Source.ScheduleMode.INTERVAL)
        )
        daily_time = attrs.get(
            "daily_check_time", getattr(instance, "daily_check_time", None)
        )
        if schedule_mode == Source.ScheduleMode.DAILY and not daily_time:
            raise serializers.ValidationError({"daily_check_time": "请选择每天检查时间。"})
        return attrs

    def create(self, validated_data):
        # New policy libraries follow the operator's current Nanning schedule unless
        # the request explicitly supplies a different value. This keeps UI and API
        # creation consistent and leaves one familiar baseline to manage.
        baseline = Source.objects.filter(adapter="nanning_v1").order_by("created_at").first()
        if baseline:
            for field in (
                "enabled",
                "interval_minutes",
                "schedule_mode",
                "daily_check_time",
            ):
                if field not in validated_data:
                    validated_data[field] = getattr(baseline, field)
        source = super().create(validated_data)
        source.next_check_at = source.next_scheduled_check()
        source.save(update_fields=["next_check_at"])
        return source

    def update(self, instance, validated_data):
        scheduling_changed = any(
            field in validated_data
            for field in {"enabled", "interval_minutes", "schedule_mode", "daily_check_time"}
        )
        source = super().update(instance, validated_data)
        if scheduling_changed:
            source.next_check_at = source.next_scheduled_check() if source.enabled else None
            source.save(update_fields=["next_check_at"])
        return source

    def _throttle(self, obj):
        if obj.adapter != "nanning_v1":
            return None
        if not hasattr(self, "_nanning_throttle"):
            self._nanning_throttle = CrawlThrottle.objects.filter(
                scope="nanning-policy-source"
            ).first()
        return self._nanning_throttle

    def get_cooldown_until(self, obj) -> datetime | None:
        throttle = self._throttle(obj)
        return throttle.blocked_until if throttle else None

    def get_cooldown_reason(self, obj) -> str:
        throttle = self._throttle(obj)
        if not throttle or not throttle.blocked_until or throttle.blocked_until <= timezone.now():
            return ""
        return {403: "来源拒绝访问", 429: "来源请求限流"}.get(
            throttle.last_status_code, "来源保护冷却"
        )

    def get_crawl_state(self, obj) -> str:
        if not obj.enabled:
            return "paused"
        throttle = self._throttle(obj)
        if throttle and throttle.blocked_until and throttle.blocked_until > timezone.now():
            return "cooldown"
        if obj.runs.filter(status__in=["queued", "running"]).exists():
            return "running"
        return "scheduled"


class SourceRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceCheckRun
        fields = [
            "id",
            "source",
            "status",
            "created_at",
            "finished_at",
            "discovered",
            "progress",
            "error_code",
            "error_message",
        ]


class SourceViewSet(viewsets.ModelViewSet):
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    serializer_class = SourceSerializer
    queryset = Source.objects.all().order_by("name", "id")
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(request=None, responses=SourceRunSerializer)
    @action(detail=True, methods=["post"], url_path="run-now")
    def run_now(self, request, pk=None):
        run = queue_check(self.get_object().id)
        return Response(SourceRunSerializer(run).data, status=202)

    def perform_destroy(self, instance):
        if instance.discovereditem_set.exists() or instance.runs.exists():
            raise serializers.ValidationError(
                "该政策库已有采集记录，不能删除。可以关闭自动检查，历史数据会继续保留。"
            )
        instance.delete()


class SourceRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SourceRunSerializer
    queryset = SourceCheckRun.objects.all()
    permission_classes = [permissions.IsAdminUser]


class DiscoveredItemSerializer(serializers.ModelSerializer):
    scope_assessment = serializers.JSONField(
        source="metadata.scope_assessment", read_only=True, default=dict
    )
    attachment_issues = serializers.JSONField(
        source="metadata.attachment_issues", read_only=True, default=list
    )

    class Meta:
        model = DiscoveredItem
        fields = [
            "id",
            "source",
            "url",
            "title",
            "status",
            "policy",
            "attempts",
            "error_code",
            "error_detail",
            "retry_at",
            "created_at",
            "scope_assessment",
            "attachment_issues",
        ]
        read_only_fields = fields


class DiscoveredItemSummarySerializer(serializers.Serializer):
    total = serializers.IntegerField()
    discovered = serializers.IntegerField()
    processing = serializers.IntegerField()
    failed = serializers.IntegerField()
    imported = serializers.IntegerField()
    indexed = serializers.IntegerField()
    excluded = serializers.IntegerField()
    needs_review = serializers.IntegerField()
    needs_attention_links = serializers.IntegerField()


class DiscoveredItemViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DiscoveredItemSerializer
    queryset = DiscoveredItem.objects.all()
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list" and self.request.query_params.get("status"):
            status = self.request.query_params["status"]
            if status not in {
                "discovered",
                "processing",
                "failed",
                "imported",
                "indexed",
                "excluded",
                "needs_review",
            }:
                raise serializers.ValidationError("未知的处理状态。")
            queryset = queryset.filter(status=status)
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "status",
                enum=[
                    "discovered",
                    "processing",
                    "failed",
                    "imported",
                    "indexed",
                    "excluded",
                    "needs_review",
                ],
            )
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=None, responses=DiscoveredItemSummarySerializer)
    @action(detail=False, methods=["get"])
    def summary(self, request):
        counts = {
            row["status"]: row["count"]
            for row in self.get_queryset().values("status").annotate(count=Count("id"))
        }
        statuses = [
            "discovered",
            "processing",
            "failed",
            "imported",
            "indexed",
            "excluded",
            "needs_review",
        ]
        payload = {status: counts.get(status, 0) for status in statuses}
        payload["total"] = sum(payload.values())
        payload["needs_attention_links"] = (
            self.get_queryset()
            .filter(status__in=["failed", "needs_review"])
            .values("url")
            .distinct()
            .count()
        )
        return Response(payload)

    @transaction.atomic
    @extend_schema(request=None, responses=DiscoveredItemSerializer)
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        item = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
        if item.status != "failed":
            raise serializers.ValidationError("只有失败的解析任务可以重试。")
        item.status, item.attempts, item.error_code, item.error_detail = "discovered", 0, "", ""
        item.retry_at = item.lease_until = None
        item.save(
            update_fields=[
                "status",
                "attempts",
                "error_code",
                "error_detail",
                "retry_at",
                "lease_until",
            ]
        )
        return Response(self.get_serializer(item).data, status=202)
