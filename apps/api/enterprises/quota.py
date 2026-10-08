"""One rolling quota for root tasks; failures release reservations.

Maintenance must be explicitly authorized, never inferred from being a retry.
These business quotas do not change per-task model/time/concurrency budgets.
"""
from datetime import timedelta

from accounts.permissions import can_manage_system
from core.models import AuditRecord
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import ResearchRun

ACTIVE = ("queued", "running", "waiting")
CHARGED = (*ACTIVE, "completed", "paused")


def window(user):
    return ResearchRun.objects.filter(user=user, parent__isnull=True, created_at__gte=timezone.now() - timedelta(hours=24))


def ensure_available(user, limit, *, exclude=None, maintenance=False):
    # Callers hold the account row lock across this check and creation/resume.
    if maintenance:
        return
    used = window(user).filter(quota_category="normal", status__in=CHARGED)
    if exclude:
        used = used.exclude(pk=exclude)
    if used.count() >= limit:
        raise ValidationError("最近24小时的 AI 整理次数已用完，可继续手动修改画像和查看匹配结果；失败和系统维护不占用次数。")


def statistics(roots):
    return roots.aggregate(
        normal=Count("pk", filter=Q(quota_category="normal", status__in=CHARGED)),
        failed=Count("pk", filter=Q(quota_category="normal", status="failed")),
        maintenance=Count("pk", filter=Q(quota_category="maintenance")),
        maintenance_failed=Count("pk", filter=Q(quota_category="maintenance", status="failed")),
    )


def record_maintenance(run, actor, reason):
    """Also supports explicit, audited reclassification of known repair runs."""
    if not can_manage_system(actor):
        raise PermissionDenied("只有系统管理员可以登记维护重跑。")
    reason = str(reason).strip()
    if not reason or len(reason) > 300:
        raise ValidationError("请填写不超过300字的维护原因。")
    with transaction.atomic():
        locked = ResearchRun.objects.select_for_update().get(pk=run.pk)
        if locked.parent_id:
            raise ValidationError("请在完整主任务上登记维护，子任务不单独计数。")
        previous = locked.quota_category
        locked.quota_category, locked.maintenance_reason = "maintenance", reason
        locked.save(update_fields=["quota_category", "maintenance_reason", "updated_at"])
        AuditRecord.objects.create(actor=actor, action="enterprise.maintenance.recorded", object_id=locked.pk,
                                  details={"reason": reason, "previous_category": previous, "owner_id": locked.user_id})
        run.quota_category, run.maintenance_reason = locked.quota_category, locked.maintenance_reason
