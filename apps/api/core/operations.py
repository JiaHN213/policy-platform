from datetime import timedelta

from django.db.models import Avg, Count, Exists, F, Min, OuterRef, Sum
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from ingestion.models import DiscoveredItem
from policies.models import PolicyEnrichment, PublicationEvent
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from subscriptions.models import Notification, PendingDelivery

from .config_views import SystemConfigPermission
from .models import AICall, AIModelProfile


class AIUsageView(APIView):
    permission_classes = [SystemConfigPermission]

    @extend_schema(responses=dict)
    def get(self, request):
        rows = AICall.objects.filter(created_at__gte=timezone.now()-timedelta(days=30))
        labels = dict(AIModelProfile.Purpose.choices)
        groups = list(rows.values("purpose", "model", "currency").annotate(
            calls=Count("id"), average_ms=Avg("duration_ms"), total_input_tokens=Sum("input_tokens"),
            total_output_tokens=Sum("output_tokens"), input_calls=Count("input_tokens"), output_calls=Count("output_tokens"), cost=Sum("estimated_cost"), priced_calls=Count("estimated_cost")))
        for group in groups:
            group["label"] = labels.get(group["purpose"], "其他用途")
            group["input_tokens"] = group.pop("total_input_tokens")
            group["output_tokens"] = group.pop("total_output_tokens")
        return Response({"items": groups, "states": list(rows.values("status").annotate(count=Count("id"))),
            "notice": "最近30天的逐次模型HTTP请求，重试分别计数。收到响应不代表审核或引用校验通过；缺失用量保持未知。费用按调用时填写的每百万Token单价估算，币种分开，不含缓存折扣等供应商规则，不是账单。只记录接入后的请求；事务回滚或记录失败可能导致缺失。"})


def pipeline_rows(mode="pending", item_id=None):
    now = timezone.now()
    query = DiscoveredItem.objects.select_related("policy", "source")
    if item_id:
        query = query.filter(pk=item_id)
    elif mode == "pending":
        excluded = PolicyEnrichment.objects.filter(policy_id=OuterRef("policy_id"), policy_version=OuterRef("policy__version"), status="succeeded", result__review__decision="exclude")
        query = query.annotate(ai_excluded=Exists(excluded)).filter(ai_excluded=False)
        query = query.exclude(status="excluded").exclude(policy__status__in=["published", "withdrawn"])
        query = query.order_by("created_at", "pk")
    else:
        query = query.order_by("-created_at", "pk")
    nodes = list(query[:100])
    ids = [item.policy_id for item in nodes if item.policy_id]
    reviews = {(r.policy_id, r.policy_version): r for r in PolicyEnrichment.objects.filter(policy_id__in=ids)}
    events = {}
    for event in PublicationEvent.objects.filter(policy_id__in=ids, policy_version=F("policy__version"), kind="policy.published.v1").order_by("created_at"):
        events.setdefault(event.policy_id, event)
    event_ids = [e.pk for e in events.values()]
    notices = {row["event_id"]: row["first"] for row in Notification.objects.filter(event_id__in=event_ids).values("event_id").annotate(first=Min("created_at"))}
    for row in PendingDelivery.objects.filter(event_id__in=event_ids, notification__isnull=False).values("event_id").annotate(first=Min("notification__created_at")):
        current = notices.get(row["event_id"])
        notices[row["event_id"]] = min(current, row["first"]) if current else row["first"]
    pending = set(PendingDelivery.objects.filter(event_id__in=event_ids, handled_at__isnull=True).values_list("event_id", flat=True))
    results = []
    for item in nodes:
        policy = item.policy
        review = reviews.get((policy.pk, policy.version)) if policy else None
        event = events.get(item.policy_id)
        notice_at = notices.get(event.pk) if event else None
        if item.status == "excluded" or (review and review.status == "succeeded" and review.result.get("review", {}).get("decision") == "exclude"):
            stage = "已排除"
        elif policy and policy.status == "withdrawn":
            stage = "已撤下"
        elif notice_at:
            stage = "已生成站内通知"
        elif event and event.pk in pending:
            stage = "等待每日汇总"
        elif event and event.delivered_at:
            stage = "发布与订阅检查完成"
        elif policy and policy.status == "published":
            stage = "已发布，等待通知检查"
        elif review and review.status == "failed":
            stage = "审核失败，需查看处理工作台"
        elif review and review.status == "succeeded":
            stage = "审核完成，待满足发布条件"
        elif policy:
            stage = "AI审核中" if review and review.status == "running" else "等待AI审核"
        else:
            stage = {"failed": "解析失败，需查看采集详情", "processing": "正在解析", "indexed": "已登记，尚未解析"}.get(item.status, "等待解析")
        elapsed = (notice_at-item.created_at).total_seconds() if notice_at and notice_at >= item.created_at else None
        waiting = stage not in {"已排除", "已撤下", "已生成站内通知", "发布与订阅检查完成"}
        results.append({"id": str(item.pk), "policy_id": str(item.policy_id) if item.policy_id else None,
            "title": item.title, "source": item.source.name, "stage": stage,
            "intake_status": item.status, "policy_status": policy.status if policy else None,
            "discovered_at": item.created_at, "parsed_at": item.parsed_at,
            "reviewed_at": review.finished_at if review and review.status == "succeeded" else None,
            "published_at": event.created_at if event else None, "notified_at": notice_at,
            "elapsed_seconds": elapsed, "overdue": waiting and now-item.created_at > timedelta(hours=24)})
    return results


class PipelineStatusView(APIView):
    permission_classes = [SystemConfigPermission]

    @extend_schema(parameters=[OpenApiParameter("item_id", type=str), OpenApiParameter("mode", enum=["pending", "recent"])], responses=dict)
    def get(self, request):
        item_id = request.query_params.get("item_id")
        if item_id:
            item_id = serializers.UUIDField().run_validation(item_id)
        return Response({"items": pipeline_rows(request.query_params.get("mode", "pending"), item_id),
            "notice": "按政策链接追踪，待处理视角展示最早100条未发布链接，近期视角展示最近100条。24小时仅作提示；解析及审核完成时间从本次升级开始记录，历史缺失不倒推。通知时间是首次生成站内通知，不代表所有用户收到或已读；无匹配订阅可正常结束而不产生通知。"})
