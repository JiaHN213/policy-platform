from django.db.models import Count
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .catalog import CatalogAdminPermission
from .event_consumers import retry_consumption
from .models import PublicationConsumption


class PublicationConsumptionSerializer(serializers.ModelSerializer):
    policy_id = serializers.UUIDField(source="event.policy_id", read_only=True)
    policy_title = serializers.CharField(source="event.policy.title", read_only=True)
    policy_version = serializers.IntegerField(source="event.policy_version", read_only=True)
    consumer_label = serializers.CharField(source="get_consumer_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    event_label = serializers.SerializerMethodField()
    can_retry = serializers.SerializerMethodField()

    class Meta:
        model = PublicationConsumption
        fields = [
            "id",
            "event",
            "policy_id",
            "policy_title",
            "policy_version",
            "event_label",
            "consumer",
            "consumer_label",
            "status",
            "status_label",
            "attempts",
            "failures",
            "retry_at",
            "last_error",
            "last_success_version",
            "succeeded_at",
            "result",
            "wiki_build",
            "can_retry",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_event_label(self, obj) -> str:
        if obj.event.kind.startswith("opportunity.deadline."):
            return "机会截止提醒"
        return {
            "policy.published.v1": "政策发布",
            "policy.corrected.v1": "人工修正",
            "policy.withdrawn.v1": "政策撤下",
        }.get(obj.event.kind, "政策更新")

    def get_can_retry(self, obj) -> bool:
        return obj.status in {"retry", "failed"}


class PublicationConsumptionViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [CatalogAdminPermission]
    serializer_class = PublicationConsumptionSerializer
    queryset = PublicationConsumption.objects.select_related("event__policy").order_by(
        "-created_at", "consumer", "id"
    )

    def get_queryset(self):
        queryset = super().get_queryset()
        for field, choices in (
            ("status", PublicationConsumption.Status.values),
            ("consumer", PublicationConsumption.Consumer.values),
        ):
            value = self.request.query_params.get(field)
            if value:
                if value not in choices:
                    raise serializers.ValidationError("请选择有效的处理环节或状态。")
                queryset = queryset.filter(**{field: value})
        keyword = self.request.query_params.get("q", "").strip()
        if keyword:
            queryset = queryset.filter(event__policy__title__icontains=keyword)
        policy_id = self.request.query_params.get("policy_id")
        if policy_id:
            field = serializers.UUIDField()
            queryset = queryset.filter(event__policy_id=field.run_validation(policy_id))
        return queryset

    @action(detail=False, methods=["get"])
    def summary(self, request):
        rows = PublicationConsumption.objects.values("consumer", "status").annotate(
            count=Count("id")
        )
        totals = {status: 0 for status in PublicationConsumption.Status.values}
        consumers = {
            key: {"label": label, "total": 0, **totals}
            for key, label in PublicationConsumption.Consumer.choices
        }
        for row in rows:
            totals[row["status"]] += row["count"]
            consumers[row["consumer"]][row["status"]] += row["count"]
            consumers[row["consumer"]]["total"] += row["count"]
        return Response(
            {
                "counts": totals,
                "consumers": consumers,
                "events": PublicationConsumption.objects.values("event_id").distinct().count(),
                "expired_leases": PublicationConsumption.objects.filter(
                    status="running", lease_until__lte=timezone.now()
                ).count(),
            }
        )

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        job = self.get_object()
        job = retry_consumption(job.pk, request.user)
        return Response(self.get_serializer(job).data)
