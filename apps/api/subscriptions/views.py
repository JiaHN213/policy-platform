from accounts.management import AccountScopeMixin
from accounts.permissions import can_manage_system
from accounts.services import access_decision
from core.business_config import get_config
from core.errors import Conflict
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_field,
    inline_serializer,
)
from policies.business_scope import configured_domains, configured_tags
from policies.models import Policy
from policies.taxonomy import OpportunityCategory, OpportunityStatus, ValidityStatus
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from .delivery import preferences, visible_notifications
from .following import RULE_FIELDS, record_change, snapshot
from .models import Notification, NotificationPreference, Subscription
from .services import match_subscription


class SubscriptionSerializer(serializers.ModelSerializer):
    topic = serializers.ChoiceField(choices=["", "水务", "环保", "人工智能＋"], required=False)
    revision = serializers.IntegerField(required=False, min_value=1)
    interest_regions = serializers.ListField(child=serializers.CharField(max_length=100), max_length=30, required=False)

    class Meta:
        model = Subscription
        fields = [
            "source_profile", "source_project", "managed", "revision", "interest_regions", "system_paused",
            "id",
            "name",
            "keywords",
            "topic",
            "document_type",
            "region",
            "target_view",
            "geographic_level",
            "province",
            "city",
            "business_domain",
            "direction_tag",
            "validity_status",
            "opportunity_category",
            "opportunity_status",
            "acquisition_method",
            "eligible_keywords",
            "authority_keywords",
            "has_deadline",
            "deadline_within_days",
            "active",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "source_profile", "source_project", "managed", "system_paused"]

    def validate(self, attrs):
        configured = get_config("system_taxonomies")

        def enabled(group):
            return {
                item["code"]
                for item in configured[group]
                if item.get("enabled", True)
            }

        checks = {
            "geographic_level": {choice[0] for choice in Policy.GeographicLevel.choices},
            "business_domain": set(configured_domains()),
            "direction_tag": set(configured_tags()),
            "validity_status": {choice[0] for choice in ValidityStatus.choices},
            "opportunity_category": {choice[0] for choice in OpportunityCategory.choices},
            "opportunity_status": {choice[0] for choice in OpportunityStatus.choices},
            "acquisition_method": enabled("acquisition_methods"),
        }
        errors = {}
        for field, allowed in checks.items():
            value = attrs.get(field, getattr(self.instance, field, "") if self.instance else "")
            if value and value not in allowed:
                errors[field] = "所选条件已停用或不存在，请重新选择。"
        deadline_days = attrs.get(
            "deadline_within_days",
            getattr(self.instance, "deadline_within_days", None) if self.instance else None,
        )
        if deadline_days is not None and not 1 <= deadline_days <= 365:
            errors["deadline_within_days"] = "截止提醒天数须在 1 至 365 天之间。"
        target = attrs.get(
            "target_view", getattr(self.instance, "target_view", "policy") if self.instance else "policy"
        )
        opportunity_fields = (
            "opportunity_category",
            "opportunity_status",
            "acquisition_method",
            "eligible_keywords",
            "authority_keywords",
            "has_deadline",
            "deadline_within_days",
        )
        if target == Subscription.TargetView.POLICY and any(
            attrs.get(field, getattr(self.instance, field, None) if self.instance else None)
            not in (None, "")
            for field in opportunity_fields
        ):
            errors["target_view"] = "设置机会条件时，请选择“政策机会”或“政策与机会”。"
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


@extend_schema(parameters=[OpenApiParameter('user_id', type=int, description='由系统管理员指定资料所属账号；省略则管理本人资料。')])
class SubscriptionViewSet(AccountScopeMixin, viewsets.ModelViewSet):
    serializer_class = SubscriptionSerializer
    queryset = Subscription.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        return Subscription.objects.filter(user=self.data_user, deleted_at__isnull=True)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        if not can_manage_system(self.request.user) and not access_decision(self.data_user, "policy_subscription")["allowed"]:
            raise PermissionDenied("当前账号无订阅权限。")
        key = request.headers.get("Idempotency-Key", "")
        if not key or len(key) > 100:
            raise ValidationError("需要长度不超过100的 Idempotency-Key。")
        # Serialize creation per user, including quota and deduplication checks.
        get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data.pop("revision", None)
        if Subscription.objects.filter(user=self.data_user, idempotency_key=key, deleted_at__isnull=False).exists():
            raise Conflict("该创建请求对应的订阅已删除，请刷新后重新创建。")
        existing = self.get_queryset().filter(idempotency_key=key).first()
        if existing:
            if any(
                getattr(existing, field) != value
                for field, value in serializer.validated_data.items()
            ):
                raise Conflict("同一幂等键对应不同的订阅内容。")
            return Response(self.get_serializer(existing).data)
        decision = access_decision(self.data_user, "policy_subscription")
        if decision.get("limit") is not None and self.get_queryset().count() >= decision["limit"]:
            raise PermissionDenied("已达到订阅数量上限。")
        serializer.save(user=self.data_user, idempotency_key=key)
        return Response(serializer.data, status=201)

    def perform_update(self, serializer):
        if not can_manage_system(self.request.user) and not access_decision(self.data_user, "policy_subscription")["allowed"]:
            raise PermissionDenied("当前账号无订阅权限。")
        with transaction.atomic():
            get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
            sub = Subscription.objects.select_for_update().get(pk=serializer.instance.pk, user=self.data_user, deleted_at__isnull=True)
            expected = serializer.validated_data.pop("revision", sub.revision)
            if expected != sub.revision:
                raise Conflict("订阅已更新，请刷新后重试。")
            before = snapshot(sub)
            serializer.instance = sub
            if any(getattr(sub, key) != value for key, value in serializer.validated_data.items()):
                sub = serializer.save(managed=False, system_paused=False, revision=sub.revision + 1)
                record_change(sub, before, "用户修改，后续自动跟随不覆盖此规则", self.request.user)

    def perform_destroy(self, instance):
        with transaction.atomic():
            get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
            sub = Subscription.objects.select_for_update().get(pk=instance.pk, user=self.data_user)
            before = snapshot(sub)
            sub.active, sub.managed = False, False
            sub.deleted_at = timezone.now()
            sub.revision += 1
            sub.save()
            record_change(sub, before, "用户删除；保留防止自动重建的记录", self.request.user)

    @extend_schema(responses=dict)
    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        sub = self.get_object()
        return Response({"revision": sub.revision, "items": list(sub.changes.values("id", "before", "after", "reason", "created_at")[:50])})

    @extend_schema(request=inline_serializer(name="RestoreSubscriptionChange", fields={"change_id": serializers.UUIDField(), "revision": serializers.IntegerField(min_value=1)}), responses=SubscriptionSerializer)
    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        if not can_manage_system(self.request.user) and not access_decision(self.data_user, "policy_subscription")["allowed"]:
            raise PermissionDenied("当前账号无订阅权限。")
        with transaction.atomic():
            get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
            sub = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
            if request.data.get("revision") != sub.revision:
                raise Conflict("订阅已变化，请刷新变更记录后重试。")
            change_id = serializers.UUIDField().run_validation(request.data.get("change_id"))
            change = sub.changes.filter(pk=change_id).first()
            if not change or not change.before:
                raise ValidationError("这条记录没有可恢复的旧设置。")
            values = {key: value for key, value in change.before.items() if key in RULE_FIELDS}
            validated = self.get_serializer(sub, data=values, partial=True)
            validated.is_valid(raise_exception=True)
            before = snapshot(sub)
            sub = validated.save(managed=False, system_paused=False, revision=sub.revision + 1)
            record_change(sub, before, "用户恢复历史设置，保留为手工管理", request.user)
        return Response(self.get_serializer(sub).data)

    @extend_schema(
        request=SubscriptionSerializer,
        responses=inline_serializer(
            name="SubscriptionPreviewResult",
            fields={
                "count": serializers.IntegerField(),
                "items": serializers.ListField(child=serializers.DictField()),
            },
        ),
    )
    @action(detail=False, methods=["post"])
    def preview(self, request):
        if not can_manage_system(self.request.user) and not access_decision(self.data_user, "policy_subscription")["allowed"]:
            raise PermissionDenied("当前账号无订阅权限。")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        values.pop("active", None)
        candidate = Subscription(
            user=self.data_user,
            idempotency_key="preview",
            active=True,
            **values,
        )
        policies = (
            Policy.objects.filter(
                status=Policy.Status.PUBLISHED,
                source_grade__in=Policy.FORMAL_SOURCE_GRADES,
            )
            .prefetch_related("opportunities__batches")
            .order_by("-publication_date", "-id")
        )
        if not self.data_user.is_staff:
            policies = policies.filter(is_demo=False)
        items = []
        count = 0
        for policy in policies:
            result = match_subscription(candidate, policy)
            if not result.matched:
                continue
            count += 1
            if len(items) < 10:
                items.append(
                    {
                        "id": str(policy.id),
                        "title": policy.title,
                        "publication_date": policy.publication_date.isoformat(),
                        "reasons": result.reasons,
                        "opportunities": result.opportunity_titles,
                    }
                )
        return Response({"count": count, "items": items})


class NotificationSerializer(serializers.ModelSerializer):
    policy_id = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_policy_id(self, obj):
        return obj.recommendation.policy_id if obj.recommendation_id else obj.event.policy_id if obj.event_id else None

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_items(self, obj):
        entries = obj.entries.select_related("event__policy").filter(event__policy__status="published", event__policy__source_grade__in=["L1", "L2", "L3"])
        if not self.context.get("data_user", self.context["request"].user).is_staff:
            entries = entries.filter(event__policy__is_demo=False)
        by_policy = {}
        for entry in entries.order_by("created_at"):
            by_policy[str(entry.event.policy_id)] = {"policy_id": str(entry.event.policy_id), "title": entry.event.policy.title, "reasons": entry.reasons}
        from enterprises.watching import recommendation_is_current, visible_recommendations
        for entry in obj.entries.filter(recommendation__in=visible_recommendations(self.context.get("data_user", self.context["request"].user))).select_related(
                "recommendation__policy", "recommendation__watch__profile", "recommendation__watch__project", "recommendation__watch__user"):
            item = entry.recommendation
            if recommendation_is_current(item):
                key = str(item.policy_id)
                reasons = list(dict.fromkeys(by_policy.get(key, {}).get("reasons", []) + entry.reasons))
                by_policy[key] = {"policy_id": key, "title": item.policy.title, "reasons": reasons}
        return list(by_policy.values())

    class Meta:
        model = Notification
        fields = ["id", "title", "policy_id", "reasons", "read_at", "created_at", "kind", "items"]


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    digest_hour = serializers.IntegerField(min_value=0, max_value=23)
    deadline_days = serializers.ListField(child=serializers.IntegerField(min_value=1, max_value=365), min_length=1, max_length=5)

    class Meta:
        model = NotificationPreference
        fields = ["update_mode", "digest_hour", "deadline_enabled", "deadline_days"]

    def validate_deadline_days(self, value):
        return sorted(set(value), reverse=True)


@extend_schema(parameters=[OpenApiParameter('user_id', type=int, description='由系统管理员指定资料所属账号；省略则管理本人资料。')])
class NotificationViewSet(AccountScopeMixin, mixins.DestroyModelMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        if not can_manage_system(self.request.user) and not access_decision(self.data_user, "policy_detail")["allowed"]:
            return self.queryset
        queryset = visible_notifications(self.data_user).select_related("event")
        if self.action == "list":
            status = self.request.query_params.get("status", "all")
            if status not in {"all", "unread", "read"}:
                raise ValidationError({"status": "请选择 all、unread 或 read。"})
            if status != "all":
                queryset = queryset.filter(read_at__isnull=status == "unread")
        return queryset

    @transaction.atomic
    def perform_destroy(self, instance):
        from core.models import AuditRecord

        from .models import PendingDelivery
        # A dismissed notification must not leave PROTECT links or be redelivered.
        PendingDelivery.objects.filter(notification=instance).update(notification=None, handled_at=timezone.now())
        AuditRecord.objects.create(actor=self.request.user, action="notification.deleted", object_id=instance.pk, details={"user_id": self.data_user.pk})
        instance.delete()

    @extend_schema(request=NotificationPreferenceSerializer, responses=NotificationPreferenceSerializer)
    @action(detail=False, methods=["get", "patch"], url_path="preferences")
    def preferences(self, request):
        with transaction.atomic():
            get_user_model().objects.select_for_update(no_key=True).get(pk=self.data_user.pk)
            preference = preferences(self.data_user.pk)
            serializer = NotificationPreferenceSerializer(preference, data=request.data, partial=True) if request.method == "PATCH" else NotificationPreferenceSerializer(preference)
            if request.method == "PATCH":
                serializer.is_valid(raise_exception=True)
                serializer.save()
        return Response(serializer.data)

    @extend_schema(parameters=[OpenApiParameter("status", enum=["all", "unread", "read"])])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request=None,
        responses=inline_serializer(
            name="MarkAllReadResult", fields={"updated": serializers.IntegerField()}
        ),
    )
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        updated = self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response({"updated": updated})

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        if not notification.read_at:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(self.get_serializer(notification).data)
