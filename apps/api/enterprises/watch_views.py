from datetime import timedelta

from accounts.management import AccountScopeMixin
from accounts.models import Organization, User
from core.config_views import SystemConfigPermission
from core.models import AuditRecord
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PolicyRecommendation, PolicyWatch, RecommendationSettings, WatchRun
from .views import profiles_for
from .watching import configuration, permitted, recommendation_is_current, visible_recommendations


class WatchInput(serializers.Serializer):
    profile = serializers.UUIDField()
    project = serializers.UUIDField(allow_null=True, default=None)
    enabled = serializers.BooleanField()
    view = serializers.ChoiceField(choices=["policies", "opportunities"], default="opportunities")
    interval_hours = serializers.ChoiceField(choices=[1, 6, 24], default=24)
    ai_explanations = serializers.BooleanField(default=False)


class WatchSerializer(serializers.ModelSerializer):
    latest_run = serializers.SerializerMethodField()

    def to_representation(self, obj):
        data = super().to_representation(obj)
        for key in ("latest_run", "next_run_at", "last_run_at", "message"):
            data.pop(key, None)
        return data

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_latest_run(self, obj):
        return None

    class Meta:
        model = PolicyWatch
        fields = [
            "id",
            "profile",
            "project",
            "enabled",
            "view",
            "interval_hours",
            "ai_explanations",
            "consent_version",
            "consented_at",
            "next_run_at",
            "last_run_at",
            "message",
            "latest_run",
        ]
        read_only_fields = fields


class RecommendationSerializer(serializers.ModelSerializer):
    title = serializers.CharField(source="policy.title", read_only=True)

    def to_representation(self, obj):
        data = super().to_representation(obj)
        data["result"] = {key: value for key, value in data["result"].items()
                          if key not in {"analysis_message", "analysis_run_id", "retrieval", "score", "runtime", "usage"}}
        return data

    class Meta:
        model = PolicyRecommendation
        fields = [
            "id",
            "watch",
            "policy",
            "title",
            "result",
            "feedback",
            "feedback_note",
            "created_at",
        ]
        read_only_fields = fields


class FeedbackInput(serializers.Serializer):
    feedback = serializers.ChoiceField(choices=["useful", "irrelevant", "missing_information"])
    feedback_note = serializers.CharField(max_length=500, allow_blank=True, default="")


@extend_schema(parameters=[OpenApiParameter('user_id', type=int, description='由系统管理员指定资料所属账号；省略则管理本人资料。')])
class WatchViewSet(AccountScopeMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = WatchSerializer
    queryset = PolicyWatch.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        return PolicyWatch.objects.filter(
            user=self.data_user, profile__in=profiles_for(self.data_user)
        ).order_by("-created_at")

    @extend_schema(request=WatchInput, responses=WatchSerializer)
    @action(detail=False, methods=["post"])
    def configure(self, request):
        payload = WatchInput(data=request.data)
        payload.is_valid(raise_exception=True)
        values = dict(payload.validated_data)
        with transaction.atomic():
            User.objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
            profile = get_object_or_404(profiles_for(self.data_user), pk=values.pop("profile"))
            project_id = values.pop("project")
            project = get_object_or_404(profile.projects, pk=project_id) if project_id else None
            watch = (
                self.get_queryset()
                .select_for_update()
                .filter(profile=profile, project=project)
                .first()
            )
            if not watch:
                if self.get_queryset().count() >= 20:
                    raise serializers.ValidationError(
                        "最多保存 20 个企业或项目关注，请修改已有关注。"
                    )
                watch = PolicyWatch(user=self.data_user, profile=profile, project=project)
            for key, value in values.items():
                setattr(watch, key, value)
            if watch.enabled and (not profile.confirmed_at or not profile.data):
                raise serializers.ValidationError("请先确认企业画像，再开启持续匹配。")
            if watch.enabled and not permitted(watch):
                raise serializers.ValidationError("当前企业或账号尚未开放持续匹配，请联系管理员。")
            watch.consent_version += 1
            watch.consented_at = timezone.now()
            watch.next_run_at = timezone.now() + timedelta(
                minutes=configuration(profile).aggregation_minutes
            )
            watch.message = (
                "等待批量检查；首次仅建立结果，后续实质变化通过站内通知提醒。"
                if watch.enabled
                else "已关闭持续匹配，不再处理或发送后续提醒。"
            )
            watch.save()
            watch.runs.filter(status__in=["queued", "running"]).update(
                status="cancelled",
                lease_token=None,
                lease_until=None,
                finished_at=timezone.now(),
                message="用户修改或关闭了关注设置。",
            )
            AuditRecord.objects.create(
                actor=request.user,
                action="watch.consent_changed",
                object_id=watch.pk,
                details={
                    "version": watch.consent_version,
                    **values,
                    "profile": str(profile.pk),
                    "project": str(project_id) if project_id else None,
                },
            )
        return Response(WatchSerializer(watch).data)

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"])
    def results(self, request):
        watch_id = serializers.UUIDField().run_validation(request.query_params.get("watch"))
        watch = get_object_or_404(self.get_queryset(), pk=watch_id)
        query = (
            visible_recommendations(self.data_user)
            .filter(watch=watch)
            .select_related("policy", "watch__profile", "watch__project", "watch__user")
        )
        # Validate before pagination so stale windows don't create empty pages or
        # inflated counts. The retained active set has one result per policy.
        items = [item for item in query if recommendation_is_current(item)]
        page = self.paginate_queryset(items)
        return self.get_paginated_response(RecommendationSerializer(page, many=True).data)

    @extend_schema(
        request=FeedbackInput,
        responses=dict,
        parameters=[
            OpenApiParameter("recommendation_id", OpenApiTypes.UUID, location=OpenApiParameter.PATH)
        ],
    )
    @action(detail=False, methods=["post"], url_path="feedback/(?P<recommendation_id>[^/.]+)")
    def feedback(self, request, recommendation_id=None):
        recommendation_id = serializers.UUIDField().run_validation(recommendation_id)
        item = get_object_or_404(
            PolicyRecommendation, pk=recommendation_id, watch__in=self.get_queryset()
        )
        payload = FeedbackInput(data=request.data)
        payload.is_valid(raise_exception=True)
        for key, value in payload.validated_data.items():
            setattr(item, key, value)
        item.save(update_fields=["feedback", "feedback_note", "updated_at"])
        return Response({"message": "反馈已记录，用于后续质量评估，不会自动改变政策或企业资料。"})


class RecommendationConfigSerializer(serializers.ModelSerializer):
    batch_size = serializers.IntegerField(min_value=1, max_value=100)
    daily_batches = serializers.IntegerField(min_value=1, max_value=2000)
    daily_model_calls = serializers.IntegerField(min_value=0, max_value=1000)
    model_calls_per_run = serializers.IntegerField(min_value=0, max_value=5)
    aggregation_minutes = serializers.IntegerField(min_value=1, max_value=60)
    retention_days = serializers.IntegerField(min_value=7, max_value=365)
    organization_options = serializers.SerializerMethodField()

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_organization_options(self, obj):
        return [
            {"value": str(pk), "label": name}
            for pk, name in Organization.objects.values_list("id", "name")
        ]

    def validate_organizations(self, values):
        ids = serializers.ListField(child=serializers.UUIDField()).run_validation(values)
        if Organization.objects.filter(pk__in=ids).count() != len(set(ids)):
            raise serializers.ValidationError("请选择有效企业。")
        return [str(pk) for pk in dict.fromkeys(ids)]

    class Meta:
        model = RecommendationSettings
        fields = [
            "enabled",
            "all_organizations",
            "organizations",
            "organization_options",
            "batch_size",
            "daily_batches",
            "daily_model_calls",
            "model_calls_per_run",
            "aggregation_minutes",
            "retention_days",
        ]


class RecommendationSettingsView(APIView):
    permission_classes = [SystemConfigPermission]

    @extend_schema(responses=RecommendationConfigSerializer)
    def get(self, request):
        return Response(RecommendationConfigSerializer(configuration()).data)

    @extend_schema(request=RecommendationConfigSerializer, responses=RecommendationConfigSerializer)
    def patch(self, request):
        with transaction.atomic():
            config = RecommendationSettings.objects.select_for_update().get(pk=configuration().pk)
            payload = RecommendationConfigSerializer(config, data=request.data, partial=True)
            payload.is_valid(raise_exception=True)
            payload.save()
            AuditRecord.objects.create(
                actor=request.user,
                action="watch.settings_changed",
                object_id=config.pk,
                details=payload.validated_data,
            )
        return Response(payload.data)


class RecommendationStatisticsView(APIView):
    permission_classes = [SystemConfigPermission]

    @extend_schema(responses=dict)
    def get(self, request):
        recent = WatchRun.objects.filter(created_at__gte=timezone.now() - timedelta(days=7))
        return Response(
            {
                "enabled_watches": PolicyWatch.objects.filter(enabled=True).count(),
                "runs": list(recent.values("status").annotate(count=Count("id"))),
                "today_batches": AuditRecord.objects.filter(
                    action="watch.batch_started", created_at__date=timezone.localdate()
                ).count(),
                "today_model_calls": AuditRecord.objects.filter(
                    action="watch.model_reserved", created_at__date=timezone.localdate()
                ).count(),
                "feedback": list(
                    PolicyRecommendation.objects.exclude(feedback="")
                    .values("feedback")
                    .annotate(count=Count("id"))
                ),
                "notice": "用户反馈是质量线索，不等于精确率或召回率。模型次数包含发起前的预算预留。",
            }
        )
