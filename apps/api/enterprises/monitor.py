from accounts.permissions import IsSystemManager
from django.db.models import Count
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .dashboard import overview, task_scope
from .models import ResearchRun
from .runtime import KINDS, STATUSES


class TaskMonitorViewSet(viewsets.GenericViewSet):
    permission_classes = [IsSystemManager]
    queryset = ResearchRun.objects.none()
    serializer_class = serializers.Serializer

    @extend_schema(responses=dict)
    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        return Response(overview(ResearchRun.objects.all(), request.query_params))

    @extend_schema(responses=dict)
    def list(self, request):
        rows, _ = task_scope(ResearchRun.objects.all(), request.query_params, default_days=7)
        state = request.query_params.get("status", "")
        if state and state not in STATUSES:
            raise serializers.ValidationError("请选择有效的执行状态。")
        if state:
            rows = rows.filter(status=state)
        page = serializers.IntegerField(min_value=1, max_value=100000).run_validation(request.query_params.get("page", 1))
        count = rows.count()
        rows = rows.annotate(calls=Count("model_requests", distinct=True), child_calls=Count("children__model_requests", distinct=True)).order_by("-created_at", "-pk")
        items = [{"id": str(row.pk), "kind_label": KINDS.get(row.kind, "资料处理"), "status": row.status,
                  "status_label": STATUSES.get(row.status, "待核对"), "created_at": row.created_at,
                  "finished_at": row.finished_at, "request_count": row.calls + row.child_calls,
                  "message": "部分结果未完成，请结合脱敏运行指标排查。" if row.status == "failed" else ""}
                 for row in rows[(page - 1) * 20:page * 20].defer("inputs", "uploaded_material", "result", "error", "checkpoint")]
        return Response({"count": count, "items": items})
