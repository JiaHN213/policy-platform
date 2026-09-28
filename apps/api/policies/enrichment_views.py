from core.ai_runtime import get_ai_profile
from core.models import AuditRecord
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .catalog import CatalogAdminPermission
from .enrichment import configured, eligible
from .models import AIReviewControl, PolicyEnrichment


class EnrichmentSerializer(serializers.ModelSerializer):
    title = serializers.CharField(source="policy.title", read_only=True)

    class Meta:
        model = PolicyEnrichment
        fields = [
            "id",
            "policy",
            "title",
            "policy_version",
            "status",
            "attempts",
            "error_code",
            "model",
            "prompt_version",
            "result",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class EnrichmentRequest(serializers.Serializer):
    policy = serializers.UUIDField()
    version = serializers.IntegerField(min_value=1)


def configuration_payload():
    counts = {
        row["status"]: row["count"]
        for row in PolicyEnrichment.objects.values("status").annotate(count=Count("id"))
    }
    return {
        "configured": configured(),
        "automation_enabled": AIReviewControl.objects.filter(
            singleton_key="default", enabled=True
        ).exists(),
        "eligible_count": eligible().count(),
        "queued_count": counts.get("queued", 0),
        "running_count": counts.get("running", 0),
        "succeeded_count": counts.get("succeeded", 0),
        "failed_count": counts.get("failed", 0),
    }


class EnrichmentViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [CatalogAdminPermission]
    serializer_class = EnrichmentSerializer
    queryset = (
        PolicyEnrichment.objects.select_related("policy")
        .annotate(
            queue_order=Case(
                When(status="running", then=Value(0)),
                When(status="queued", then=Value(1)),
                When(status="failed", then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        )
        .order_by("queue_order", "created_at", "id")
    )

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"], url_path="configuration")
    def configuration(self, request):
        return Response({**configuration_payload(), "model": get_ai_profile("review").model})

    def _set_automation(self, request, enabled):
        if enabled and not configured():
            raise ValidationError("模型尚未配置，不能开始自动审核。")
        with transaction.atomic():
            control = (
                AIReviewControl.objects.select_for_update().filter(singleton_key="default").first()
            )
            if control is None:
                control = AIReviewControl.objects.create(singleton_key="default")
            control.enabled = enabled
            control.updated_by = request.user
            control.save(update_fields=["enabled", "updated_by", "updated_at"])
            AuditRecord.objects.create(
                actor=request.user,
                action="policy.ai_review.automation_started"
                if enabled
                else "policy.ai_review.automation_stopped",
                object_id=control.pk,
                details={"enabled": enabled},
            )
        return Response({**configuration_payload(), "model": get_ai_profile("review").model})

    @extend_schema(request=None, responses=dict)
    @action(detail=False, methods=["post"], url_path="start-automation")
    def start_automation(self, request):
        return self._set_automation(request, True)

    @extend_schema(request=None, responses=dict)
    @action(detail=False, methods=["post"], url_path="stop-automation")
    def stop_automation(self, request):
        return self._set_automation(request, False)

    @extend_schema(request=EnrichmentRequest, responses=EnrichmentSerializer)
    @action(detail=False, methods=["post"], url_path="enqueue")
    def enqueue(self, request):
        payload = EnrichmentRequest(data=request.data)
        payload.is_valid(raise_exception=True)
        if not configured():
            raise ValidationError("请先在本地 .env 配置 DeepSeek API 地址、模型与密钥。")
        with transaction.atomic():
            policy = get_object_or_404(
                eligible().select_for_update(),
                pk=payload.validated_data["policy"],
                version=payload.validated_data["version"],
            )
            job, _ = PolicyEnrichment.objects.get_or_create(
                policy=policy,
                policy_version=policy.version,
                defaults={"prompt_version": "policy-enrichment-v4"},
            )
        return Response(EnrichmentSerializer(job).data, status=202)

    @extend_schema(request=None, responses=EnrichmentSerializer)
    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, pk=None):
        if not configured():
            raise ValidationError("模型尚未配置。")
        with transaction.atomic():
            job = get_object_or_404(PolicyEnrichment.objects.select_for_update(), pk=pk)
            if job.status not in {"failed", "running"} or (
                job.lease_until and job.lease_until > timezone.now()
            ):
                raise ValidationError("仅可重试失败或已超时的任务。")
            if not eligible().filter(pk=job.policy_id, version=job.policy_version).exists():
                raise ValidationError("来源版本已变化或不再允许处理，请选择当前正式来源版本。")
            job.status, job.attempts, job.retry_at, job.lease_until, job.error_code = (
                "queued",
                0,
                None,
                None,
                "",
            )
            job.save()
        return Response(EnrichmentSerializer(job).data)
