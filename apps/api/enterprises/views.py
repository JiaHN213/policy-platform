import hashlib
import json
from datetime import timedelta
from pathlib import PurePath

from accounts.management import AccountScopeMixin
from accounts.models import Membership, Organization
from accounts.permissions import IsSystemManager, can_manage_system
from core.ai_runtime import get_ai_profile
from core.config_views import SystemConfigPermission
from core.errors import Conflict
from core.models import AuditRecord
from core.pagination import PagePagination
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from policies.catalog import formal_policies
from policies.search import SearchRequest
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .fields import FIELDS, tag_options, validate_data
from .matching import INACTIVE, LEVELS, match_policies
from .materials import (
    CUSTOMER_MATERIAL_ERRORS,
    FILE_TYPES,
    MAX_UPLOAD,
    MaterialError,
    normalize_website,
)
from .models import EnterpriseProfile, EnterpriseProject, ResearchRun, ResearchSettings
from .policy_evidence import has_active_opportunity, policy_signature
from .scoped_settings import research_configuration
from .search import SearchUnavailable, searxng_request, validate_searxng_url
from .subscriptions import subscribe_to_profile
from .tasks import enqueue


def profiles_for(user):
    return EnterpriseProfile.objects.filter(organization__membership__user=user,
                                            organization__membership__active=True).select_related("organization")


def require_editor(profile, user):
    if not can_manage_system(user) and not Membership.objects.filter(organization=profile.organization, user=user, active=True, role="admin").exists():
        raise PermissionDenied("只有企业管理员可以修改画像和项目。")


def delete_profile_data(profile):
    from subscriptions.models import PendingDelivery, Subscription

    from .models import WatchRun
    if ResearchRun.objects.filter(profile=profile, status__in=["queued", "running", "waiting"]).exists() or WatchRun.objects.filter(watch__profile=profile, status__in=["queued", "running"]).exists():
        raise Conflict("企业仍有资料整理或匹配任务，请等待结束或停止后再删除。")
    PendingDelivery.objects.filter(recommendation__watch__profile=profile).delete()
    Subscription.objects.filter(source_profile=profile).delete()
    profile.organization.delete()


class ProfileSubscriptionResult(serializers.Serializer):
    created_count = serializers.IntegerField()
    existing_count = serializers.IntegerField()
    status = serializers.ChoiceField(choices=["created", "unchanged", "unavailable"])
    message = serializers.CharField()


class ProfileSerializer(serializers.ModelSerializer):
    matching_task = serializers.JSONField(read_only=True, default=None)
    name = serializers.CharField(source="organization.name", read_only=True)
    can_edit = serializers.SerializerMethodField()
    follow_subscriptions = serializers.SerializerMethodField()
    follow_status = serializers.SerializerMethodField()
    subscription_result = ProfileSubscriptionResult(read_only=True, allow_null=True, default=None)

    class Meta:
        model = EnterpriseProfile
        fields = ["id", "name", "data", "evidence", "revision", "confirmed_at", "updated_at", "can_edit", "refresh_days", "next_research_at", "research_method", "subscription_result", "follow_subscriptions", "follow_status", "matching_task"]
        read_only_fields = fields

    @extend_schema_field(serializers.BooleanField())
    def get_can_edit(self, obj):
        return can_manage_system(self.context["request"].user) or obj.organization.membership_set.filter(user=self.context.get("data_user", self.context["request"].user), active=True, role="admin").exists()

    @extend_schema_field(serializers.BooleanField())
    def get_follow_subscriptions(self, obj):
        from subscriptions.models import ProfileFollow
        return ProfileFollow.objects.filter(user=self.context.get("data_user", self.context["request"].user), profile=obj, project__isnull=True, enabled=True).exists()

    @extend_schema_field(serializers.CharField())
    def get_follow_status(self, obj):
        from subscriptions.models import ProfileFollow
        follow = ProfileFollow.objects.filter(user=self.context.get("data_user", self.context["request"].user), profile=obj, project__isnull=True).first()
        return follow.last_error if follow else ""


class ProfileInput(serializers.Serializer):
    start_matching = serializers.BooleanField(default=False)
    name = serializers.CharField(max_length=200)
    data = serializers.JSONField(default=dict)
    revision = serializers.IntegerField(min_value=1, required=False)
    run_id = serializers.UUIDField(required=False)
    candidate_index = serializers.IntegerField(min_value=0, max_value=3, default=0)
    refresh_days = serializers.ChoiceField(choices=[0, 7, 30, 90], required=False)
    auto_subscribe = serializers.BooleanField(default=False)
    follow_subscriptions = serializers.BooleanField(required=False)

    def validate_data(self, value):
        return validate_data(value)


class SubscriptionPlanInput(serializers.Serializer):
    project_id = serializers.UUIDField(required=False, allow_null=True)
    revision = serializers.IntegerField(min_value=1, required=False)
    enabled = serializers.BooleanField(required=False)


def confirmation_evidence(user, inputs, profile=None):
    evidence, candidate = {}, None
    if inputs.get("run_id"):
        run = get_object_or_404(ResearchRun, pk=inputs["run_id"], user=user, kind="company", status="completed")
        from .runtime import input_issue
        issue = input_issue(run)
        if issue:
            raise Conflict(issue)
        if run.profile_id and (not profile or run.profile_id != profile.pk):
            raise ValidationError("这份资料草稿属于另一家企业。")
        if run.agent_snapshot and profile and run.agent_snapshot["profile_revision"] != profile.revision:
            raise Conflict("这份补查草稿基于旧画像，请重新整理，避免覆盖更新后的信息。")
        candidates = run.result.get("candidates", [])
        index = inputs["candidate_index"]
        if index >= len(candidates):
            raise ValidationError("请选择有效的企业候选。")
        candidate = candidates[index]
        if candidate["name"] != inputs["name"]:
            raise ValidationError("所选企业名称与草稿不一致，请重新查找或手动建档。")
    for key, value in inputs["data"].items():
        # Keep prior evidence only when the user actually kept the same value.
        if profile and profile.data.get(key) == value and key in profile.evidence:
            evidence[key] = profile.evidence[key]
        elif candidate and candidate["data"].get(key) == value:
            evidence[key] = {"origin": "资料提取，经用户确认", "sources": candidate["evidence"].get(key, []), "confirmed_at": timezone.now().isoformat()}
        else:
            evidence[key] = {"origin": "用户填写或修改", "sources": [], "confirmed_at": timezone.now().isoformat()}
    return evidence


def confirmed_method(user, values, profile=None):
    method = profile.research_method if profile else "materials"
    if values.get("run_id"):
        run = get_object_or_404(ResearchRun, pk=values["run_id"], user=user, kind="company", status="completed")
        mode = run.inputs.get("source_mode", "search")
        method = mode if mode in {"website", "search"} else "materials"
    days = values.get("refresh_days", profile.refresh_days if profile else 0)
    if method == "materials":
        days = 0
    if method == "website" and days and not values["data"].get("website"):
        raise ValidationError("官网定期检查需要保留官网地址，或将检查频率改为仅手动。")
    return method, days


def start_after_confirmation(user, profile, values):
    if not values.get("start_matching"):
        return
    from .workflow import start
    try:
        source = ResearchRun.objects.filter(pk=values.get("run_id"), user=user, parent__kind="workflow", kind="company").select_related("parent").first() if values.get("run_id") else None
        context = source.parent.inputs if source else {}
        project = profile.projects.filter(pk=context.get("project_id")).first() if context.get("project_id") else None
        if context.get("project_id") and not project:
            raise Conflict("原匹配项目已删除，请重新选择项目后启动匹配。")
        run = start(user, profile, source_run=values.get("run_id"), project=project,
                    view=context.get("matching_view", "opportunities"), filters=context.get("filters"), fill_gaps=not bool(source))
        profile.matching_task = {"id": str(run.pk), "message": "画像已保存，正在自动检索并解读相关政策。"}
    except APIException as exc:
        # Optional AI work must never roll back a valid profile confirmation.
        profile.matching_task = {"id": None, "message": "画像已保存，自动匹配暂未启动：" + str(exc.detail)}


class WorkflowInput(serializers.Serializer):
    project_id = serializers.UUIDField(required=False)
    view = serializers.ChoiceField(choices=["policies", "opportunities"], default="opportunities")
    filters = serializers.JSONField(default=dict)
    level = serializers.ChoiceField(choices=["", *LEVELS], default="")

    def validate_filters(self, value):
        if not isinstance(value, dict):
            raise ValidationError("请选择有效的筛选条件。")
        allowed = set(SearchRequest().fields) - {"page", "page_size", "view", "mode"}
        if set(value) - allowed - {"include_conflicts"}:
            raise ValidationError("包含不支持的筛选条件，请刷新页面。")
        data = SearchRequest(data={key: val for key, val in value.items() if key in allowed and val != ""})
        data.is_valid(raise_exception=True)
        result = json.loads(json.dumps(data.validated_data, default=str))
        result["include_conflicts"] = False
        return result


@extend_schema(parameters=[OpenApiParameter('user_id', type=int, description='由系统管理员指定资料所属账号；省略则管理本人资料。')])
class EnterpriseViewSet(AccountScopeMixin, viewsets.GenericViewSet):
    serializer_class = ProfileSerializer
    pagination_class = None
    queryset = EnterpriseProfile.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        return profiles_for(self.data_user).order_by("created_at")

    @extend_schema(responses=dict, operation_id="v1_enterprises_list")
    def list(self, request):
        return Response({"items": self.get_serializer(self.get_queryset(), many=True).data})

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    @extend_schema(request=ProfileInput, responses=ProfileSerializer)
    def create(self, request):
        serializer = ProfileInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        evidence = confirmation_evidence(self.data_user, values)
        with transaction.atomic():
            get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
            if self.get_queryset().count() >= 10:
                raise ValidationError("每个账号最多管理10家企业。")
            if self.get_queryset().filter(organization__name=values["name"]).exists():
                raise ValidationError("你已经建立了这家企业的画像，请打开已有画像修改。")
            # Never join another customer's organization just because its name matches.
            organization = Organization.objects.create(name=values["name"])
            Membership.objects.create(user=self.data_user, organization=organization, role="admin")
            method, days = confirmed_method(self.data_user, values)
            profile = EnterpriseProfile.objects.create(organization=organization, data=values["data"], evidence=evidence, confirmed_at=timezone.now(), refresh_days=days,
                                                        next_research_at=timezone.now() + timedelta(days=days) if days else None, research_method=method)
            AuditRecord.objects.create(actor=request.user, action="enterprise.created", object_id=profile.pk,
                                       details={"source_run_id": str(values["run_id"])} if values.get("run_id") else {})
            if values.get("follow_subscriptions"):
                from subscriptions.following import configure_follow
                profile.subscription_result = configure_follow(self.data_user, profile, True)
            elif values["auto_subscribe"]:
                profile.subscription_result = subscribe_to_profile(self.data_user, profile)
            start_after_confirmation(self.data_user, profile, values)
        return Response(self.get_serializer(profile).data, status=201)

    @extend_schema(request=ProfileInput, responses=ProfileSerializer)
    def partial_update(self, request, pk=None):
        serializer = ProfileInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        with transaction.atomic():
            get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
            profile = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
            require_editor(profile, self.request.user)
            if values.get("revision") != profile.revision:
                raise Conflict("画像已被更新，请刷新后重新核对，避免覆盖其他成员的修改。")
            if values["name"] != profile.organization.name:
                if self.get_queryset().exclude(pk=profile.pk).filter(organization__name=values["name"]).exists():
                    raise ValidationError("该账号已存在同名企业，请核对名称。")
                profile.organization.name = values["name"]
                profile.organization.save(update_fields=["name"])
            before = profile.data
            profile.evidence = confirmation_evidence(self.data_user, values, profile)
            profile.data = values["data"]
            profile.revision += 1
            profile.confirmed_at = timezone.now()
            profile.research_method, profile.refresh_days = confirmed_method(self.data_user, values, profile)
            profile.next_research_at = timezone.now() + timedelta(days=profile.refresh_days) if profile.refresh_days else None
            profile.save()
            AuditRecord.objects.create(actor=request.user, action="enterprise.confirmed", object_id=profile.pk,
                                       details={"before": before, "after": profile.data, "revision": profile.revision,
                                                **({"source_run_id": str(values["run_id"])} if values.get("run_id") else {})})
            from subscriptions.following import configure_follow
            if "follow_subscriptions" in values:
                profile.subscription_result = configure_follow(self.data_user, profile, values["follow_subscriptions"])
            else:
                configure_follow(self.data_user, profile)
            if values["auto_subscribe"] and not values.get("follow_subscriptions"):
                profile.subscription_result = subscribe_to_profile(self.data_user, profile)
            start_after_confirmation(self.data_user, profile, values)
        return Response(self.get_serializer(profile).data)

    @transaction.atomic
    def destroy(self, request, pk=None):
        get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
        profile = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
        require_editor(profile, request.user)
        if request.data.get("confirm_name") != profile.organization.name:
            raise ValidationError("请输入企业名称确认删除。")
        AuditRecord.objects.create(actor=request.user, action="enterprise.deleted", object_id=profile.pk)
        delete_profile_data(profile)
        return Response(status=204)

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"], url_path="options")
    def configuration(self, request):
        config = research_configuration(self.data_user)
        return Response({"fields": FIELDS, "tags": tag_options(), "search_ready": config.search_ready,
                         "ai_ready": get_ai_profile("enterprise").configured,
                         "matching_ai_ready": get_ai_profile("enterprise_match").configured, "levels": LEVELS,
                         "workflow_max_policies": config.workflow_max_policies})

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def matches(self, request, pk=None):
        profile = self.get_object()
        project_id = request.query_params.get("project_id")
        if project_id:
            project_id = serializers.UUIDField().run_validation(project_id)
        project = get_object_or_404(profile.projects, pk=project_id) if project_id else None
        view = request.query_params.get("view", "policies")
        if view not in {"policies", "opportunities"}:
            raise ValidationError("请选择政策或政策机会视角。")
        inputs = {key: request.query_params[key] for key in SearchRequest().fields if key in request.query_params}
        inputs["view"] = "opportunity" if view == "opportunities" else "policy"
        filters = SearchRequest(data=inputs)
        filters.is_valid(raise_exception=True)
        params = dict(filters.validated_data)
        params["include_conflicts"] = serializers.BooleanField().run_validation(request.query_params.get("include_conflicts", False))
        result = match_policies(self.data_user, profile, project, view, params)
        level = request.query_params.get("level", "")
        if level and level not in LEVELS:
            raise ValidationError("请选择有效的相关度。")
        if level:
            result["items"] = [item for item in result["items"] if item["level"] == level]
        result["count"] = len(result["items"])
        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except ValueError as exc:
            raise ValidationError("页码须为数字。") from exc
        result["items"] = result["items"][(page - 1) * 20:page * 20]
        result.pop("search_backend", None)
        for item in result["items"]:
            item.pop("retrieval", None)
            item.pop("score", None)
        return Response(result)

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"], url_path="home")
    def home(self, request, pk=None):
        profile = self.get_object()
        project_id = request.query_params.get("project_id")
        if project_id:
            project_id = serializers.UUIDField().run_validation(project_id)
        project = get_object_or_404(profile.projects, pk=project_id) if project_id else None
        rows = match_policies(self.data_user, profile, project, "opportunities")["items"]
        relevant = [row for row in rows if row["level"] in {"high", "medium", "insufficient"} and row["conditions"]["status"] != "conflict"]
        for row in relevant:
            row.pop("retrieval", None)
            row.pop("score", None)
        recommended = [row for row in relevant if row["recommendation_group"] == "priority"]
        uncertain = [row for row in relevant if row["recommendation_group"] != "priority"]
        from django.utils.dateparse import parse_datetime
        now = timezone.now()
        deadlines = []
        for row in relevant:
            deadline = parse_datetime(row["deadline"]) if row.get("deadline") else None
            if deadline and timezone.is_naive(deadline):
                deadline = timezone.make_aware(deadline)
            if deadline and now <= deadline <= now + timedelta(days=14):
                deadlines.append((deadline, row))
        deadlines = [row for _, row in sorted(deadlines, key=lambda item: item[0])]
        return Response({"recommended": recommended[:6], "uncertain": uncertain[:6], "deadlines": deadlines[:5],
                         "counts": {"recommended": len(recommended), "uncertain": len(uncertain)},
                         "notice": "推荐依据已确认资料与政策原文，仅供研究参考；资料不足不等于不符合条件。"})

    @extend_schema(request=SubscriptionPlanInput, responses=dict)
    @action(detail=True, methods=["get", "post"], url_path="subscription-plan")
    def subscription_plan(self, request, pk=None):
        from policies.business_scope import configured_domains
        from subscriptions.following import configure_follow
        from subscriptions.models import ProfileFollow
        payload = SubscriptionPlanInput(data=request.data if request.method == "POST" else request.query_params)
        payload.is_valid(raise_exception=True)
        values = payload.validated_data
        with transaction.atomic():
            if request.method == "POST":
                get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
            profile = self.get_object()
            if request.method == "POST":
                profile = EnterpriseProfile.objects.select_for_update().get(pk=profile.pk)
            project = get_object_or_404(profile.projects.select_for_update() if request.method == "POST" else profile.projects, pk=values["project_id"]) if values.get("project_id") else None
            revision = project.revision if project else profile.revision
            data = project.data if project else profile.data
            labels = configured_domains()
            regions = data.get("interest_regions", []) or ([data["city"]] if project and data.get("city") else [])
            rules = [{"name": f"{project.name if project else profile.organization.name} · {labels[domain][0]}"[:100],
                      "business_domain": domain, "label": labels[domain][0], "target_view": "all", "interest_regions": regions}
                     for domain in dict.fromkeys(data.get("business_domains", [])) if domain in labels]
            if request.method == "POST":
                if "enabled" not in values or values.get("revision") != revision:
                    raise Conflict("资料或订阅范围已变化，请刷新预览后再次确认。")
                if values["enabled"] and not rules:
                    raise ValidationError("请先补充企业或项目的业务领域。")
                return Response(configure_follow(self.data_user, profile, values["enabled"], project))
            follow = ProfileFollow.objects.filter(user=self.data_user, profile=profile, project=project).first()
            return Response({"revision": revision, "rules": rules, "enabled": bool(follow and follow.enabled),
                             "notice": "按业务领域与关注地区生成订阅，不把发文地区当作申报资格。开启后跟随已确认资料更新，手工修改和关闭的规则不会被覆盖。"})

    @extend_schema(request=WorkflowInput, responses=dict)
    @action(detail=True, methods=["post"], url_path="matching-workflow")
    def matching_workflow(self, request, pk=None):
        from .workflow import start
        serializer = WorkflowInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        profile = self.get_object()
        project = get_object_or_404(profile.projects, pk=values["project_id"]) if values.get("project_id") else None
        filters = {**values["filters"], "level": values["level"]}
        run = start(self.data_user, profile, project=project, view=values["view"], filters=filters)
        return Response({"id": str(run.pk), "status": run.status}, status=202)


class ProjectSerializer(serializers.ModelSerializer):
    revision = serializers.IntegerField(min_value=1, required=False)
    follow_subscriptions = serializers.SerializerMethodField()
    follow_status = serializers.SerializerMethodField()

    @extend_schema_field(serializers.BooleanField())
    def get_follow_subscriptions(self, obj):
        from subscriptions.models import ProfileFollow
        return ProfileFollow.objects.filter(user=self.context.get("data_user", self.context["request"].user), project=obj, enabled=True).exists()

    @extend_schema_field(serializers.CharField())
    def get_follow_status(self, obj):
        from subscriptions.models import ProfileFollow
        return ProfileFollow.objects.filter(user=self.context.get("data_user", self.context["request"].user), project=obj).values_list("last_error", flat=True).first() or ""

    class Meta:
        model = EnterpriseProject
        fields = ["id", "profile", "name", "description", "data", "revision", "updated_at", "follow_subscriptions", "follow_status"]
        read_only_fields = ["id", "updated_at"]
        extra_kwargs = {"description": {"max_length": 4000}}

    def validate_profile(self, value):
        if not profiles_for(self.context.get("data_user", self.context["request"].user)).filter(pk=value.pk).exists():
            raise PermissionDenied("无法访问该企业。")
        require_editor(value, self.context["request"].user)
        if self.instance and self.instance.profile_id != value.pk:
            raise ValidationError("不能更换项目所属企业。")
        return value

    def validate_data(self, value):
        if not isinstance(value, dict) or set(value) - {"province", "city", "business_domains", "direction_tags", "interest_regions", "project_stage", "investment_wan"}:
            raise ValidationError("项目仅支持地点、业务领域、技术方向、项目阶段和总投资。")
        return validate_data(value, project=True)


class ProjectPagination(PagePagination):
    page_size = 100


class ProjectFollowInput(serializers.Serializer):
    enabled = serializers.BooleanField()
    revision = serializers.IntegerField(min_value=1)


@extend_schema(parameters=[OpenApiParameter('user_id', type=int, description='由系统管理员指定资料所属账号；省略则管理本人资料。')])
class ProjectViewSet(AccountScopeMixin, viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    queryset = EnterpriseProject.objects.none()
    pagination_class = ProjectPagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    @extend_schema(request=ProjectFollowInput, responses=ProjectSerializer)
    @action(detail=True, methods=["post"], url_path="follow-subscriptions")
    def follow_subscriptions(self, request, pk=None):
        from subscriptions.following import configure_follow
        data = ProjectFollowInput(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
            candidate = get_object_or_404(self.get_queryset(), pk=pk)
            EnterpriseProfile.objects.select_for_update().get(pk=candidate.profile_id)
            project = get_object_or_404(self.get_queryset().select_for_update(of=("self",)), pk=pk)
            if project.revision != data.validated_data["revision"]:
                raise Conflict("项目已更新，请刷新后重新确认订阅范围。")
            configure_follow(self.data_user, project.profile, data.validated_data["enabled"], project)
        return Response(self.get_serializer(project).data)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = EnterpriseProject.objects.filter(profile__in=profiles_for(self.data_user)).order_by("created_at")
        profile_id = self.request.query_params.get("profile")
        return qs.filter(profile_id=serializers.UUIDField().run_validation(profile_id)) if profile_id else qs

    def perform_create(self, serializer):
        with transaction.atomic():
            profile = EnterpriseProfile.objects.select_for_update().get(pk=serializer.validated_data["profile"].pk)
            require_editor(profile, self.request.user)
            if profile.projects.count() >= 100:
                raise ValidationError("每家企业最多保存100个项目。")
            project = serializer.save(revision=1)
            AuditRecord.objects.create(actor=self.request.user, action="enterprise.project.created", object_id=project.pk)

    def perform_update(self, serializer):
        with transaction.atomic():
            project = EnterpriseProject.objects.select_for_update().get(pk=serializer.instance.pk)
            require_editor(project.profile, self.request.user)
            if serializer.validated_data.get("revision") != project.revision:
                raise Conflict("项目已被更新，请刷新后重试。")
            serializer.instance = project
            serializer.save(revision=project.revision + 1)
            AuditRecord.objects.create(actor=self.request.user, action="enterprise.project.updated", object_id=project.pk)

    def perform_destroy(self, instance):
        require_editor(instance.profile, self.request.user)
        AuditRecord.objects.create(actor=self.request.user, action="enterprise.project.deleted", object_id=instance.pk)
        instance.delete()


class ResearchInput(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["company", "project", "explanation"], default="company")
    name = serializers.CharField(max_length=200, required=False)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    credit_code = serializers.RegexField(r"^[A-Z0-9]{18}$", required=False, allow_blank=True)
    website = serializers.CharField(max_length=500, required=False, allow_blank=True)
    description = serializers.CharField(max_length=4000, required=False)
    profile_id = serializers.UUIDField(required=False)
    project_id = serializers.UUIDField(required=False)
    policy_id = serializers.UUIDField(required=False)
    matching_view = serializers.ChoiceField(choices=["policies", "opportunities"], default="policies")
    source_mode = serializers.ChoiceField(choices=["search", "website", "text", "file"], default="search")
    introduction = serializers.CharField(min_length=20, max_length=40000, required=False)
    file = serializers.FileField(required=False, write_only=True, max_length=200)
    retry_run_id = serializers.UUIDField(required=False, write_only=True)

    def validate_website(self, value):
        if not value:
            return value
        try:
            return normalize_website(value)
        except MaterialError as exc:
            raise ValidationError(str(exc)) from exc

    def validate(self, attrs):
        if attrs["kind"] != "company":
            for key in ("introduction", "file", "retry_run_id"):
                if key in attrs:
                    raise ValidationError("项目和解读任务不接受企业介绍文件。")
            return attrs
        mode = attrs["source_mode"]
        if mode == "website":
            if not attrs.get("website"):
                raise ValidationError("请填写企业官网地址。")
            try:
                attrs["website"] = normalize_website(attrs["website"])
            except MaterialError as exc:
                raise ValidationError(str(exc)) from exc
        if mode == "text" and not attrs.get("introduction"):
            raise ValidationError("请粘贴至少20字的企业简介。")
        if mode == "file" and not attrs.get("file") and not attrs.get("retry_run_id"):
            raise ValidationError("请选择企业介绍文件。")
        upload = attrs.get("file")
        if upload and (upload.size > MAX_UPLOAD or not upload.size or PurePath(upload.name).suffix.lower() not in FILE_TYPES):
            raise ValidationError("请上传不超过5MB的 PDF、DOCX、PPTX、TXT 或 Markdown 文件。")
        # Do not carry inactive form inputs into search or model requests.
        if mode != "text":
            attrs.pop("introduction", None)
        if mode != "file":
            attrs.pop("file", None)
            attrs.pop("retry_run_id", None)
        return attrs


class MaintenanceRerunInput(serializers.Serializer):
    maintenance = serializers.BooleanField(default=False)
    maintenance_reason = serializers.CharField(max_length=300, required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["maintenance"] and not attrs.get("maintenance_reason", "").strip():
            raise ValidationError("请填写维护原因。")
        return attrs


class EnterpriseResearchRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResearchRun
        fields = ["id", "kind", "profile", "inputs", "status", "stage", "result", "error", "created_at", "finished_at"]
        read_only_fields = fields

    def to_representation(self, obj):
        # Customer contracts expose results, not execution traces or model settings.
        result = super().to_representation(obj)
        for key in ("agent", "stage", "finished_at"):
            result.pop(key, None)
        result["status"] = "completed" if obj.status == "completed" else "needs_information" if obj.status in {"failed", "paused"} else "pending"
        result["inputs"] = {key: obj.inputs[key] for key in ("name", "source_mode", "website", "description", "profile_id", "policy_id", "project_id", "matching_view") if key in obj.inputs}
        result["error"] = "暂未获得可用结果，请核对所提供的资料，或稍后重新提交。" if obj.status in {"failed", "paused"} else ""
        if obj.status in {"failed", "paused"} and obj.error in CUSTOMER_MATERIAL_ERRORS:
            result["error"] = obj.error
        if obj.status != "completed":
            result["result"] = {}
        else:
            allowed = ("candidates", "warnings", "sources", "notice", "name", "description", "data", "points", "conditions", "additional_conditions", "coverage", "policy_id")
            result["result"] = {key: value for key, value in obj.result.items() if key in allowed}
        return result


@extend_schema(parameters=[OpenApiParameter('user_id', type=int, description='由系统管理员指定资料所属账号；省略则管理本人资料。')])
class ResearchViewSet(AccountScopeMixin, viewsets.GenericViewSet):
    serializer_class = EnterpriseResearchRunSerializer
    queryset = ResearchRun.objects.none()
    pagination_class = None

    def get_permissions(self):
        if self.action in {"task_list", "task_detail", "dashboard", "stop", "resume"}:
            return [IsSystemManager()]
        return super().get_permissions()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        return ResearchRun.objects.filter(user=self.data_user).filter(
            Q(profile__isnull=True) | Q(profile__in=profiles_for(self.data_user))
        )

    @extend_schema(responses=dict, operation_id="v1_enterprise_research_list")
    def list(self, request):
        from .runtime import input_issue
        items = []
        for run in self.get_queryset().filter(status="completed")[:30]:
            if input_issue(run):
                continue
            if AuditRecord.objects.filter(action__in=["enterprise.created", "enterprise.confirmed"], details__source_run_id=str(run.pk)).exists():
                continue
            item = self.get_serializer(run).data
            # Inbox previews are metadata; full private results remain in retrieve.
            item["result"] = {}
            items.append(item)
        return Response({"items": items})

    def retrieve(self, request, pk=None):
        run = self.get_object()
        if run.runtime_snapshot:
            from .runtime import input_issue
            issue = input_issue(run)
            if issue:
                raise Conflict(issue)
        if run.profile_id and not profiles_for(self.data_user).filter(pk=run.profile_id).exists():
            raise PermissionDenied("企业成员权限已经变化。")
        if run.kind == "explanation":
            policy = formal_policies(self.data_user).filter(pk=run.inputs["policy_id"]).first()
            project = run.profile.projects.filter(pk=run.inputs.get("project_id")).first() if run.inputs.get("project_id") else None
            if not policy or policy.version != run.inputs["policy_version"] or run.profile.revision != run.inputs["profile_revision"] or (run.inputs.get("project_id") and (not project or project.revision != run.inputs["project_revision"])):
                raise Conflict("政策、画像或项目已更新，请重新生成解读。")
            if run.inputs.get("matching_signature") and policy_signature(self.data_user, policy) != run.inputs["matching_signature"]:
                raise Conflict("政策条件、资料或申报批次已变化，请重新分析。")
        data = self.get_serializer(run).data
        if run.kind == "workflow":
            from .presentation import workflow_result
            data["result"] = workflow_result(run)
        return Response(data)

    @extend_schema(parameters=[OpenApiParameter("kind", enum=["company", "project", "explanation", "workflow"]),
        OpenApiParameter("status", enum=["queued", "running", "waiting", "completed", "failed", "paused"]),
        OpenApiParameter("page", type=int), OpenApiParameter("days", type=int, enum=[1, 7, 30, 90])], responses=dict)
    @action(detail=False, methods=["get"], url_path="tasks")
    def task_list(self, request):
        from .dashboard import task_scope
        from .runtime import KINDS, STATUSES, task_summary
        queryset, _ = task_scope(self.get_queryset(), request.query_params)
        queryset = queryset.select_related("user", "profile__organization").prefetch_related("steps", "model_requests")
        for key, choices in (("kind", KINDS), ("status", STATUSES)):
            value = request.query_params.get(key)
            if value:
                if value not in choices:
                    raise ValidationError("请选择有效的任务类型或状态。")
                queryset = queryset.filter(**{key: value})
        page = serializers.IntegerField(min_value=1, max_value=100000).run_validation(request.query_params.get("page", 1))
        return Response({"count": queryset.count(), "items": [task_summary(run) for run in queryset.order_by("-created_at", "-pk")[(page-1)*20:page*20]]})

    @extend_schema(parameters=[OpenApiParameter("kind", enum=["company", "project", "explanation", "workflow"]),
        OpenApiParameter("days", type=int, enum=[1, 7, 30, 90])], responses=dict)
    @action(detail=False, methods=["get"], url_path="dashboard")
    def dashboard(self, request):
        from .dashboard import overview
        return Response(overview(self.get_queryset(), request.query_params))

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"], url_path="task")
    def task_detail(self, request, pk=None):
        from .runtime import task_summary
        return Response(task_summary(self.get_object(), details=True))

    @extend_schema(request=None, responses=EnterpriseResearchRunSerializer)
    @action(detail=True, methods=["post"])
    def stop(self, request, pk=None):
        run = self.get_object()
        if run.parent_id:
            raise ValidationError("请在所属自动匹配主任务中停止整个流程。")
        with transaction.atomic():
            run = ResearchRun.objects.select_for_update().get(pk=run.pk)
            if run.status in {"queued", "running", "waiting"}:
                if run.status == "running" and run.started_at:
                    run.usage["seconds"] = max(run.usage.get("seconds", 0), run.usage.get("attempt_base_seconds", 0) + max(0, (timezone.now() - run.started_at).total_seconds()))
                run.status, run.lease_token, run.stage = "paused", None, "已停止，现有画像不受影响"
                run.finished_at = timezone.now()
                run.save()
                if run.kind == "workflow":
                    from .workflow import stop
                    stop(run)
        return Response(self.get_serializer(run).data)

    @extend_schema(request=None, responses=EnterpriseResearchRunSerializer)
    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        from .agent import enabled_for, signature

        with transaction.atomic():
            get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
            run = self.get_queryset().select_for_update(of=("self",)).get(pk=self.get_object().pk)
            if run.parent_id:
                raise ValidationError("请在所属自动匹配主任务中继续未完成的分析。")
            if not (run.agent_snapshot or run.runtime_snapshot) or run.status not in {"paused", "failed"}:
                raise ValidationError("当前任务不能继续，请重新整理资料。")
            if run.profile_id and run.kind not in {"explanation", "workflow"}:
                if not run.profile.organization.membership_set.filter(user=run.user, active=True, role="admin").exists():
                    raise PermissionDenied("任务所属账号已失去企业编辑权限，不能继续整理资料。")
                require_editor(run.profile, self.request.user)
            from .runtime import input_issue
            issue = input_issue(run, configuration=True)
            if issue:
                raise Conflict(issue)
            config = research_configuration(self.data_user, run.profile)
            revision = run.profile.revision if run.profile_id else None
            if run.agent_snapshot and (not enabled_for(config, run.profile) or signature(config) != run.agent_snapshot["signature"] or revision != run.agent_snapshot["profile_revision"]):
                raise Conflict("画像、开放范围或配置已变化，请基于当前资料重新创建任务。")
            if run.attempts >= 3 or (run.agent_snapshot and (run.usage.get("calls", 0) >= run.agent_snapshot["max_calls"] or run.usage.get("seconds", 0) >= run.agent_snapshot["max_seconds"])):
                raise ValidationError("本次任务的恢复次数或补查预算已用完，请核对已保存结果或重新整理。")
            if ResearchRun.objects.filter(user=self.data_user, parent__isnull=True, status__in=["queued", "running", "waiting"]).count() >= config.active_limit:
                raise ValidationError("已有任务在处理，请稍后继续。")
            from .quota import ensure_available
            ensure_available(self.data_user, config.daily_limit, exclude=run.pk, maintenance=run.quota_category == "maintenance")
            run.status, run.started_at, run.error, run.retry_at = "queued", None, "", None
            run.finished_at = None
            run.attempts += 1
            run.stage = "等待从保存的进度继续" if run.agent_snapshot else "等待重新执行，保留原任务记录"
            if run.kind == "workflow":
                from .workflow import prepare_resume
                prepare_resume(run)
                run.stage = "等待继续未完成的政策分析"
            run.save()
            enqueue(run)
        return Response(self.get_serializer(run).data)

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"], url_path="saved-results")
    def saved_results(self, request):
        page = serializers.IntegerField(min_value=1).run_validation(request.query_params.get("page", 1))
        qs = self.get_queryset().filter(parent__isnull=True).order_by("-created_at")
        if request.query_params.get("profile_id"):
            profile_id = serializers.UUIDField().run_validation(request.query_params["profile_id"])
            qs = qs.filter(profile_id=profile_id)
        return Response({"count": qs.count(), "items": [{"id": str(run.pk), "kind": run.kind, "name": run.inputs.get("name") or (run.profile.organization.name if run.profile_id else "政策分析"), "status": run.status, "created_at": run.created_at, "profile_id": str(run.profile_id) if run.profile_id else None, **({"quota_category": run.quota_category} if can_manage_system(request.user) else {})} for run in qs.select_related("profile__organization")[(page-1)*20:page*20]]})

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"], url_path="saved-result")
    def saved_result(self, request, pk=None):
        run = self.get_object()
        if run.kind == "explanation" and not formal_policies(self.data_user).filter(pk=run.inputs.get("policy_id")).exists():
            raise PermissionDenied("该分析对应的政策已撤下或不再可访问。")
        # A historical result stays inspectable but is never presented as current.
        from .presentation import workflow_result
        from .runtime import input_issue
        return Response({"id": str(run.pk), "kind": run.kind, "stale_reason": input_issue(run) or "", "result": workflow_result(run) if run.kind == "workflow" else run.result})

    @transaction.atomic
    def destroy(self, request, pk=None):
        get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
        run = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
        if run.parent_id:
            raise ValidationError("请删除整份分析结果，不能单独删除其中的步骤。")
        if run.status in {"queued", "running", "waiting"} or run.children.filter(status__in=["queued", "running", "waiting"]).exists():
            raise Conflict("资料仍在处理中，请等待完成或停止后再删除。")
        AuditRecord.objects.create(actor=request.user, action="enterprise.result.deleted", object_id=run.pk)
        run.delete()
        return Response(status=204)

    @extend_schema(request=MaintenanceRerunInput, responses=EnterpriseResearchRunSerializer)
    @action(detail=True, methods=["post"])
    def regenerate(self, request, pk=None):
        options = MaintenanceRerunInput(data=request.data)
        options.is_valid(raise_exception=True)
        maintenance = options.validated_data["maintenance"]
        reason = options.validated_data.get("maintenance_reason", "")
        if maintenance and not can_manage_system(request.user):
            raise PermissionDenied("只有系统管理员可以发起维护重跑。")
        run = self.get_object()
        if not self.data_user.is_active:
            raise ValidationError("该账号已停用，不能启动新的资料分析。")
        if run.parent_id:
            raise ValidationError("请从完整分析结果重新生成。")
        if run.status in {"queued", "running", "waiting"}:
            raise Conflict("这份资料仍在处理，请等待结束。")
        if run.kind == "workflow":
            from .workflow import start
            if not run.profile_id:
                raise ValidationError("企业已删除，请重新建档。")
            project = get_object_or_404(run.profile.projects, pk=run.inputs["project_id"]) if run.inputs.get("project_id") else None
            result = start(self.data_user, run.profile, project=project, view=run.inputs.get("matching_view", "opportunities"), filters=run.inputs.get("filters", {}), maintenance_actor=request.user if maintenance else None, maintenance_reason=reason)
            return Response(self.get_serializer(result).data, status=202)
        values = {key: value for key, value in run.inputs.items() if key in ResearchInput().fields}
        values["kind"] = run.kind
        if run.profile_id:
            values["profile_id"] = str(run.profile_id)
            if run.kind == "company":
                values["name"] = run.profile.organization.name
        if run.kind == "company" and values.get("source_mode") == "file":
            values["retry_run_id"] = str(run.pk)
        return self.create_result(request, values, force=True, maintenance=maintenance, maintenance_reason=reason)

    @extend_schema(request=ResearchInput, responses=EnterpriseResearchRunSerializer)
    def create(self, request):
        return self.create_result(request, request.data)

    def create_result(self, request, source_data, *, force=False, maintenance=False, maintenance_reason=""):
        if maintenance and not can_manage_system(request.user):
            raise PermissionDenied("只有系统管理员可以发起维护重跑。")
        if not self.data_user.is_active:
            raise ValidationError("该账号已停用，不能启动新的资料分析。")
        serializer = ResearchInput(data=source_data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        upload = values.pop("file", None)
        retry_id = values.pop("retry_run_id", None)
        material = None
        if retry_id:
            previous = get_object_or_404(self.get_queryset(), pk=retry_id, kind="company")
            if previous.inputs.get("source_mode") != "file" or not previous.uploaded_material or previous.inputs.get("name") != values.get("name"):
                raise ValidationError("原文件不可复用，请重新上传介绍文件。")
            material = bytes(previous.uploaded_material)
            values["file_name"] = previous.inputs["file_name"]
        if upload:
            material = upload.read(MAX_UPLOAD + 1)
            if len(material) > MAX_UPLOAD:
                raise ValidationError("企业介绍文件不能超过5MB。")
            values["file_name"] = upload.name.replace("\\", "/").rsplit("/", 1)[-1]
        if material:
            values["file_sha256"] = hashlib.sha256(material).hexdigest()
        inputs = json.loads(json.dumps(values, default=str))
        kind = inputs.pop("kind")
        profile = get_object_or_404(profiles_for(self.data_user), pk=inputs["profile_id"]) if inputs.get("profile_id") else None
        if kind == "company" and not inputs.get("name"):
            raise ValidationError("请填写企业名称。")
        if kind != "company" and not profile:
            raise ValidationError("请先建立并选择企业画像。")
        if profile and kind != "explanation":
            require_editor(profile, self.request.user)
        if profile and kind == "company" and inputs["name"] != profile.organization.name:
            raise ValidationError("请使用当前企业名称更新资料。")
        if kind == "project" and not inputs.get("description"):
            raise ValidationError("请用一句话描述拟申报项目。")
        if kind == "explanation":
            if not inputs.get("policy_id"):
                raise ValidationError("请选择需要解读的政策。")
            policy = get_object_or_404(formal_policies(self.data_user), pk=inputs["policy_id"])
            if policy.validity_status in INACTIVE or (inputs.get("matching_view") == "opportunities" and not has_active_opportunity(self.data_user, policy)):
                raise Conflict("政策或申报窗口已变化，当前不参与匹配，请刷新结果。")
            project = get_object_or_404(profile.projects, pk=inputs["project_id"]) if inputs.get("project_id") else None
            inputs.update(profile_revision=profile.revision, policy_version=policy.version,
                          project_revision=project.revision if project else None,
                          matching_signature=policy_signature(self.data_user, policy))
        if not get_ai_profile("enterprise_match" if kind == "explanation" else "enterprise").configured:
            raise ValidationError("请先在系统配置中设置企业画像与匹配使用的 AI 模型，也可以直接手动建档。")
        config = research_configuration(self.data_user, profile)
        if not (config.matching_allowed if kind == "explanation" else config.research_allowed):
            raise ValidationError("当前企业暂未开放这项资料整理或匹配服务，请联系管理员；已有结果仍可查看。")
        if kind == "company" and inputs.get("source_mode", "search") == "search" and not config.search_ready:
            raise ValidationError("联网查资料尚未启用。管理员可启用本地 SearXNG 或填写其他服务密钥；也可使用官网、上传或粘贴简介。")
        from .agent import snapshot_for
        from .grounding import VALIDATION_VERSION
        from .runtime import snapshot
        runtime_snapshot = snapshot(profile, config, kind=kind)
        agent_snapshot = snapshot_for(profile, user=self.data_user) if kind == "company" else {}
        fingerprint = hashlib.sha256(json.dumps([VALIDATION_VERSION, kind, inputs, agent_snapshot, runtime_snapshot], sort_keys=True).encode()).hexdigest()
        with transaction.atomic():
            get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
            config = research_configuration(self.data_user, profile)
            if not (config.matching_allowed if kind == "explanation" else config.research_allowed):
                raise ValidationError("当前企业暂未开放这项资料整理或匹配服务，请联系管理员。")
            cached = self.get_queryset().filter(fingerprint=fingerprint, quota_category="maintenance" if maintenance else "normal", created_at__gte=timezone.now() - timedelta(hours=24), status__in=["queued", "running", "completed"]).first()
            if cached and (not force or cached.status in {"queued", "running"}):
                return Response(self.get_serializer(cached).data)
            if ResearchRun.objects.filter(user=self.data_user, parent__isnull=True, status__in=["queued", "running", "waiting"]).count() >= config.active_limit:
                raise ValidationError("已有任务正在处理，请等待完成后再试。")
            from .quota import ensure_available, record_maintenance
            ensure_available(self.data_user, config.daily_limit, maintenance=maintenance)
            run = ResearchRun.objects.create(user=self.data_user, profile=profile, kind=kind, inputs=inputs, fingerprint=fingerprint, uploaded_material=material, agent_snapshot=agent_snapshot, runtime_snapshot=runtime_snapshot)
            if maintenance:
                record_maintenance(run, request.user, maintenance_reason)
            enqueue(run)
        return Response(self.get_serializer(run).data, status=202)


class ResearchSettingsSerializer(serializers.ModelSerializer):
    workflow_gap_fill = serializers.BooleanField(required=False)
    workflow_concurrency = serializers.IntegerField(min_value=1, max_value=3, required=False)
    workflow_cache_hours = serializers.IntegerField(min_value=0, max_value=72, required=False)
    workflow_retry_limit = serializers.IntegerField(min_value=0, max_value=3, required=False)
    workflow_daily_calls = serializers.IntegerField(min_value=1, max_value=500, required=False)
    workflow_max_policies = serializers.IntegerField(min_value=1, max_value=10, required=False)
    workflow_max_calls = serializers.IntegerField(min_value=1, max_value=20, required=False)
    workflow_max_seconds = serializers.IntegerField(min_value=60, max_value=900, required=False)
    agent_organizations = serializers.ListField(child=serializers.UUIDField(), required=False, max_length=50)
    organization_options = serializers.SerializerMethodField()
    agent_max_reads = serializers.IntegerField(min_value=1, max_value=5, required=False)
    agent_max_calls = serializers.IntegerField(min_value=2, max_value=10, required=False)
    agent_max_seconds = serializers.IntegerField(min_value=60, max_value=480, required=False)

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_organization_options(self, obj):
        return [{"value": str(o.pk), "label": o.name} for o in Organization.objects.filter(enterpriseprofile__isnull=False).order_by("name")]

    def validate_agent_organizations(self, values):
        if Organization.objects.filter(pk__in=values).count() != len(set(values)):
            raise ValidationError("部分试点企业不存在，请重新选择。")
        return list(dict.fromkeys(str(v) for v in values))
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=1000)
    has_api_key = serializers.SerializerMethodField()
    clear_api_key = serializers.BooleanField(write_only=True, required=False, default=False)
    max_sources = serializers.IntegerField(min_value=2, max_value=8)
    daily_limit = serializers.IntegerField(min_value=1, max_value=100)

    class Meta:
        model = ResearchSettings
        fields = ["enabled", "provider", "searxng_url", "api_key", "has_api_key", "clear_api_key", "max_sources", "daily_limit", "agent_enabled", "agent_all_organizations", "agent_organizations", "agent_max_reads", "agent_max_calls", "agent_max_seconds", "organization_options", "workflow_max_policies", "workflow_max_calls", "workflow_max_seconds", "workflow_gap_fill", "workflow_concurrency", "workflow_cache_hours", "workflow_retry_limit", "workflow_daily_calls"]

    @extend_schema_field(serializers.BooleanField())
    def get_has_api_key(self, obj):
        return bool(obj.api_key)

    def validate_searxng_url(self, value):
        try:
            return validate_searxng_url(value)
        except (ValueError, SearchUnavailable) as exc:
            raise ValidationError(str(exc)) from exc

    def validate(self, attrs):
        provider = attrs.get("provider", self.instance.provider)
        if provider == "searxng":
            self.validate_searxng_url(attrs.get("searxng_url", self.instance.searxng_url))
            return attrs
        if provider != self.instance.provider and not attrs.get("api_key"):
            raise ValidationError("切换搜索服务时，请填写新服务的密钥。")
        if attrs.get("enabled", self.instance.enabled) and not attrs.get("clear_api_key") and not (attrs.get("api_key") or self.instance.api_key):
            raise ValidationError("请先填写搜索服务密钥，再开启联网查资料。")
        return attrs

    def update(self, instance, validated_data):
        if not validated_data.get("api_key"):
            validated_data.pop("api_key", None)
        if validated_data.pop("clear_api_key", False):
            validated_data["api_key"] = ""
            if validated_data.get("provider", instance.provider) != "searxng":
                validated_data["enabled"] = False
        return super().update(instance, validated_data)


class SearchConnectionInput(serializers.Serializer):
    mode = serializers.ChoiceField(choices=["connection", "search"])
    query = serializers.CharField(max_length=200, default="南宁 水务 企业")


class SearchConnectionOutput(serializers.Serializer):
    connected = serializers.BooleanField()
    message = serializers.CharField()
    result_count = serializers.IntegerField(required=False)
    unavailable_engines = serializers.IntegerField(required=False)
    items = serializers.ListField(child=serializers.DictField(), required=False)


class ResearchSettingsView(APIView):
    permission_classes = [SystemConfigPermission]

    @extend_schema(responses=ResearchSettingsSerializer)
    def get(self, request):
        config, _ = ResearchSettings.objects.get_or_create(key="default")
        return Response(ResearchSettingsSerializer(config).data)

    @extend_schema(request=ResearchSettingsSerializer, responses=ResearchSettingsSerializer)
    def patch(self, request):
        config, _ = ResearchSettings.objects.get_or_create(key="default")
        serializer = ResearchSettingsSerializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        AuditRecord.objects.create(actor=request.user, action="enterprise.search.configured", object_id=config.pk,
                                   details={"provider": config.provider, "enabled": config.enabled, "agent_enabled": config.agent_enabled, "agent_all_organizations": config.agent_all_organizations, "agent_organizations": config.agent_organizations, "agent_max_reads": config.agent_max_reads, "agent_max_calls": config.agent_max_calls, "agent_max_seconds": config.agent_max_seconds})
        return Response(serializer.data)

    @extend_schema(request=SearchConnectionInput, responses=SearchConnectionOutput)
    def post(self, request):
        serializer = SearchConnectionInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        config, _ = ResearchSettings.objects.get_or_create(key="default")
        if config.provider != "searxng":
            raise ValidationError("请先选择并保存 SearXNG，再进行测试。")
        try:
            if serializer.validated_data["mode"] == "connection":
                return Response(searxng_request(config, use_cache=False))
            result = searxng_request(config, serializer.validated_data["query"], use_cache=False)
        except SearchUnavailable as exc:
            raise ValidationError(str(exc)) from exc
        items = [{"title": str(row.get("title", ""))[:300], "url": str(row.get("url", ""))[:2000]}
                 for row in result["results"][:config.max_sources] if isinstance(row, dict)]
        count = len(items)
        message = (f"搜索可用，返回 {count} 条参考结果。" if count else
                   "服务已连接，但上游引擎未正常返回结果，请检查网络或稍后重试。" if result["unresponsive_count"] else
                   "搜索请求成功，本次没有结果；可以更换企业名称或关键词。")
        return Response({"connected": True, "message": message, "result_count": count,
                         "unavailable_engines": result["unresponsive_count"], "items": items})
