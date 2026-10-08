from core.models import AuditRecord
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .catalog import CatalogAdminPermission
from .models import AIReviewControl, PolicyEnrichment, ReviewRecovery
from .recovery import enqueue, stop


class RecoverySerializer(serializers.ModelSerializer):
    policy_title = serializers.CharField(source="policy.title", read_only=True)
    details = serializers.SerializerMethodField()

    @extend_schema_field(serializers.DictField())
    def get_details(self, obj) -> dict:
        return {k: v for k, v in obj.result.items() if k not in {"checkpoints"}}

    class Meta:
        model = ReviewRecovery
        fields = ["id", "policy", "policy_title", "source_job", "policy_version", "status", "category",
                  "stage", "message", "attempts", "retry_at", "automatic", "details", "created_at", "updated_at"]
        read_only_fields = fields


class RecoveryRequest(serializers.Serializer):
    enrichment = serializers.UUIDField()


class RecoverySettings(serializers.ModelSerializer):
    recovery_daily_limit = serializers.IntegerField(min_value=1, max_value=200)
    recovery_attempt_limit = serializers.IntegerField(min_value=1, max_value=3)
    recovery_cooldown_minutes = serializers.IntegerField(min_value=5, max_value=1440)

    class Meta:
        model = AIReviewControl
        fields = ["recovery_enabled", "recovery_daily_limit", "recovery_attempt_limit", "recovery_cooldown_minutes"]


class RecoveryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [CatalogAdminPermission]
    serializer_class = RecoverySerializer
    queryset = ReviewRecovery.objects.select_related("policy", "source_job").all()

    def get_queryset(self):
        queryset = super().get_queryset()
        policy_id = self.request.query_params.get("policy_id")
        if policy_id:
            queryset = queryset.filter(policy_id=serializers.UUIDField().run_validation(policy_id))
        return queryset

    @extend_schema(parameters=[OpenApiParameter("policy_id", type=str)])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request=RecoveryRequest, responses=RecoverySerializer)
    @action(detail=False, methods=["post"])
    def enqueue(self, request):
        payload = RecoveryRequest(data=request.data)
        payload.is_valid(raise_exception=True)
        job = get_object_or_404(PolicyEnrichment.objects.select_related("policy"), pk=payload.validated_data["enrichment"])
        try:
            record = enqueue(job, request.user)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(RecoverySerializer(record).data, status=202)

    @extend_schema(request=None, responses=RecoverySerializer)
    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        record = self.get_object()
        stop(record)
        record.refresh_from_db()
        return Response(RecoverySerializer(record).data)

    @extend_schema(request=None, responses=RecoverySerializer)
    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        with transaction.atomic():
            record = get_object_or_404(ReviewRecovery.objects.select_for_update(), pk=pk)
            control, _ = AIReviewControl.objects.get_or_create(singleton_key="default")
            if record.status not in {"failed", "cancelled"} or record.attempts >= control.recovery_attempt_limit:
                raise serializers.ValidationError("当前任务已完成或达到次数上限，请人工处理。")
            if record.retry_at and record.retry_at > timezone.now():
                raise serializers.ValidationError("当前仍在冷却时间内，请稍后继续。")
            if record.policy.version != record.policy_version:
                raise serializers.ValidationError("原文版本已变化，请从当前政策重新发起。")
            record.status, record.automatic, record.requested_by = "queued", False, request.user
            record.lease_until = None
            record.stage = "等待继续处理"
            record.save()
        return Response(RecoverySerializer(record).data)

    @extend_schema(request=RecoverySettings, responses=RecoverySettings)
    @action(detail=False, methods=["get", "patch"], url_path="settings")
    def recovery_settings(self, request):
        control, _ = AIReviewControl.objects.get_or_create(singleton_key="default")
        if request.method == "PATCH":
            payload = RecoverySettings(control, data=request.data, partial=True)
            payload.is_valid(raise_exception=True)
            with transaction.atomic():
                payload.save()
                AuditRecord.objects.create(actor=request.user, action="policy.recovery.settings_changed",
                    object_id=control.pk, details=payload.validated_data)
        return Response(RecoverySettings(control).data)
