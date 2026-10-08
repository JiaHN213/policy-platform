"""Read-only, account-scoped enterprise task metrics; no copied task state."""
from datetime import datetime, time, timedelta

from core.models import AICall
from django.db.models import Avg, Case, CharField, Count, F, Q, Value, When
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import serializers

from .quota import statistics
from .runtime import KINDS, STATUSES


def task_scope(queryset, params, *, default_days=None):
    kind = params.get("kind", "")
    if kind and kind not in KINDS:
        raise serializers.ValidationError("请选择有效的任务类型。")
    days = params.get("days", default_days)
    if days is not None:
        days = serializers.ChoiceField(choices=[1, 7, 30, 90]).run_validation(days)
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today - timedelta(days=days - 1), time.min))
        end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
        queryset = queryset.filter(created_at__gte=start, created_at__lt=end)
    if kind:
        queryset = queryset.filter(kind=kind)
    return queryset.filter(parent__isnull=True), days


REASONS = [
    ("预算或次数已用完", ["预算", "额度", "次数", "上限"], "查看任务用量；达到当日限额时次日再试，其他情况请管理员核对配置。"),
    ("依据或访问权限已变化", ["更新", "变化", "权限", "撤下", "删除", "关闭"], "核对最新企业资料和政策后重新创建任务。"),
    ("服务超时或连接异常", ["超时", "时间过长", "中断", "连接", "繁忙", "网络", "timeout"], "查看模型与后台服务状态；可恢复的任务可继续执行。"),
    ("资料或证据不足", ["证据", "引用", "资料不足", "材料不足", "原文"], "查看原始材料和分析依据，补充资料后重新处理。"),
    ("模型输出或解析异常", ["格式", "解析", "结构", "json"], "核对模型配置和输入材料，再尝试恢复任务。"),
]


def overview(visible, params):
    roots, days = task_scope(visible, params, default_days=7)
    # Subqueries preserve ownership/access checks and avoid multiplying calls by joins.
    nodes = visible.filter(Q(pk__in=roots.values("pk")) | Q(parent__in=roots.values("pk")))
    calls = AICall.objects.filter(task__in=nodes.values("pk"))
    grouped = dict(roots.order_by().values("status").annotate(n=Count("pk")).values_list("status", "n"))
    counts = {key: grouped.get(key, 0) for key in STATUSES}
    finished = counts["completed"] + counts["failed"]
    durations = roots.filter(status="completed", finished_at__gte=F("created_at")).aggregate(
        mean=Avg(F("finished_at") - F("created_at")))
    usage = calls.aggregate(total=Count("pk"), failed=Count("pk", filter=Q(status__in=["failed", "timeout"])),
                            average_ms=Avg("duration_ms"))
    explanations = nodes.filter(kind="explanation", status="completed")
    reused = explanations.filter(checkpoint__reused_from__isnull=False).exclude(checkpoint__reused_from="").count()
    explanation_count = explanations.count()
    # A failed coordinator and its failed children describe one failure, not two.
    failed_parents = roots.filter(status="failed").exclude(children__status="failed").values("pk")
    failures = nodes.filter(status="failed").filter(Q(parent__isnull=False) | Q(pk__in=failed_parents))
    cases = []
    for label, words, _ in REASONS:
        condition = Q()
        for word in words:
            condition |= Q(error__icontains=word)
        cases.append(When(condition, then=Value(label)))
    buckets = failures.annotate(reason=Case(*cases, default=Value("其他未完成原因"), output_field=CharField())).order_by().values("reason").annotate(count=Count("pk")).order_by("-count", "reason")
    advice = {label: tip for label, _, tip in REASONS}
    reasons = [{**row, "suggestion": advice.get(row["reason"], "打开任务过程查看具体原因和可用操作。")} for row in buckets]
    dates = {row["day"]: row for row in roots.order_by().annotate(day=TruncDate("created_at")).values("day").annotate(
        total=Count("pk"), completed=Count("pk", filter=Q(status="completed")), failed=Count("pk", filter=Q(status="failed")))}
    today = timezone.localdate()
    trend = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        trend.append(dates.get(day, {"day": day, "total": 0, "completed": 0, "failed": 0}))
    return {"days": days, "generated_at": timezone.now(), "total": sum(grouped.values()), "statuses": counts,
            "failure_rate": round(counts["failed"] / finished * 100, 1) if finished else None,
            "average_completion_seconds": round(durations["mean"].total_seconds(), 1) if durations["mean"] is not None else None,
            "duration_samples": roots.filter(status="completed", finished_at__gte=F("created_at")).count(),
            "calls": {**usage, "average_ms": round(usage["average_ms"], 1) if usage["average_ms"] is not None else None},
            "reuse": {"count": reused, "completed_explanations": explanation_count,
                      "rate": round(reused / explanation_count * 100, 1) if explanation_count else None},
            "retry_waiting": nodes.filter(status="queued", retry_at__gt=timezone.now()).count(),
            "quota_statistics": statistics(roots), "trend": trend, "failure_reasons": reasons}
