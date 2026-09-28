from accounts.services import access_decision
from core.errors import Conflict
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from core.business_config import get_config
from policies.business_scope import configured_domains, configured_tags
from policies.models import Policy
from policies.taxonomy import OpportunityCategory, OpportunityStatus, ValidityStatus
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from .models import Notification, Subscription
from .services import match_subscription


class SubscriptionSerializer(serializers.ModelSerializer):
    topic = serializers.ChoiceField(choices=["", "水务", "环保", "人工智能＋"], required=False)

    class Meta:
        model = Subscription
        fields = [
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
        read_only_fields = ["id", "created_at"]

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


class SubscriptionViewSet(viewsets.ModelViewSet):
    serializer_class = SubscriptionSerializer
    queryset = Subscription.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        return Subscription.objects.filter(user=self.request.user)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        if not access_decision(request.user, "policy_subscription")["allowed"]:
            raise PermissionDenied("当前账号无订阅权限。")
        key = request.headers.get("Idempotency-Key", "")
        if not key or len(key) > 100:
            raise ValidationError("需要长度不超过100的 Idempotency-Key。")
        # Serialize creation per user, including quota and deduplication checks.
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        existing = self.get_queryset().filter(idempotency_key=key).first()
        if existing:
            if any(
                getattr(existing, field) != value
                for field, value in serializer.validated_data.items()
            ):
                raise Conflict("同一幂等键对应不同的订阅内容。")
            return Response(self.get_serializer(existing).data)
        decision = access_decision(request.user, "policy_subscription")
        if decision["limit"] is not None and self.get_queryset().count() >= decision["limit"]:
            raise PermissionDenied("已达到订阅数量上限。")
        serializer.save(user=request.user, idempotency_key=key)
        return Response(serializer.data, status=201)

    def perform_update(self, serializer):
        if not access_decision(self.request.user, "policy_subscription")["allowed"]:
            raise PermissionDenied("当前账号无订阅权限。")
        serializer.save()

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
        if not access_decision(request.user, "policy_subscription")["allowed"]:
            raise PermissionDenied("当前账号无订阅权限。")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        values.pop("active", None)
        candidate = Subscription(
            user=request.user,
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
        if not request.user.is_staff:
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
    policy_id = serializers.UUIDField(source="event.policy_id", read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "title", "policy_id", "reasons", "read_at", "created_at"]


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        if not access_decision(self.request.user, "policy_detail")["allowed"]:
            return self.queryset
        queryset = Notification.objects.select_related("event").filter(
            user=self.request.user,
            event__policy__status=Policy.Status.PUBLISHED,
            event__policy__source_grade__in=Policy.FORMAL_SOURCE_GRADES,
        )
        if not self.request.user.is_staff:
            queryset = queryset.filter(event__policy__is_demo=False)
        if self.action == "list":
            status = self.request.query_params.get("status", "all")
            if status not in {"all", "unread", "read"}:
                raise ValidationError({"status": "请选择 all、unread 或 read。"})
            if status != "all":
                queryset = queryset.filter(read_at__isnull=status == "unread")
        return queryset

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
