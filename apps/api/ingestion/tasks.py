from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from .gov_library import SourceUnavailable, fetch_list_page
from .importer import import_record
from .models import DiscoveredItem, Source, SourceCheckRun


@transaction.atomic
def queue_check(source_id):
    source = Source.objects.select_for_update().get(pk=source_id)
    now = timezone.now()
    active = source.runs.filter(status__in=["queued", "running"], lease_until__gt=now).first()
    if active:
        return active
    if source.adapter in {"nanning_v1", "gov_library_html_v1"}:
        unfinished = (
            source.runs.filter(status__in=["queued", "running", "failed"])
            .exclude(progress={})
            .order_by("-created_at")
            .first()
        )
        if unfinished:
            unfinished.status = "queued"
            unfinished.error_code = unfinished.error_message = ""
            unfinished.finished_at = None
            unfinished.lease_until = now + timedelta(minutes=10)
            unfinished.save(
                update_fields=[
                    "status",
                    "error_code",
                    "error_message",
                    "finished_at",
                    "lease_until",
                ]
            )
            minimum_interval = settings.POLICY_CRAWL_SOURCE_INTERVAL_MINUTES
            source.next_check_at = source.next_scheduled_check(now, minimum_interval)
            source.save(update_fields=["next_check_at"])
            if not settings.LOCAL_WORKER:
                transaction.on_commit(lambda: check_source.delay(str(unfinished.id)))
            return unfinished
    source.runs.filter(status__in=["queued", "running"]).update(
        status="failed", error_code="LEASE_EXPIRED", finished_at=now
    )
    progress = {}
    if source.adapter == "nanning_v1" and source.last_success_at:
        from .nanning import new_progress

        start = (
            source.last_success_at - timedelta(days=settings.POLICY_CRAWL_INCREMENTAL_LOOKBACK_DAYS)
        ).date()
        progress = new_progress(start.isoformat(), now.date().isoformat())
    run = SourceCheckRun.objects.create(
        source=source, lease_until=now + timedelta(minutes=10), progress=progress
    )
    minimum_interval = settings.POLICY_CRAWL_SOURCE_INTERVAL_MINUTES
    source.next_check_at = source.next_scheduled_check(now, minimum_interval)
    source.save(update_fields=["next_check_at"])
    if not settings.LOCAL_WORKER:
        transaction.on_commit(lambda: check_source.delay(str(run.id)))
    return run


@shared_task
def dispatch_due_sources():
    now = timezone.now()
    ids = (
        Source.objects.filter(enabled=True)
        .filter(Q(next_check_at__isnull=True) | Q(next_check_at__lte=now))
        .values_list("id", flat=True)
    )
    for source_id in list(ids)[:20]:
        queue_check(source_id)


@shared_task
def check_source(run_id):
    # A compare-and-set claim prevents concurrent redelivery executing the same run.
    if not SourceCheckRun.objects.filter(pk=run_id, status="queued").update(status="running"):
        return
    run = SourceCheckRun.objects.select_related("source").get(pk=run_id)
    try:
        if run.source.adapter == "nanning_v1":
            from .nanning import advance

            advance(run_id)
            if (
                not settings.LOCAL_WORKER
                and SourceCheckRun.objects.filter(pk=run_id, status="queued").exists()
            ):
                check_source.delay(str(run_id))
            return
        if run.source.adapter == "gov_library_html_v1":
            from .gov_library import advance_full_scan

            advance_full_scan(run_id, fetcher=fetch_list_page)
            if (
                not settings.LOCAL_WORKER
                and SourceCheckRun.objects.filter(pk=run_id, status="queued").exists()
            ):
                check_source.delay(str(run_id))
            return
        if run.source.adapter not in {"nanning_v1", "gov_library_html_v1"}:
            raise SourceUnavailable("ADAPTER_NOT_SUPPORTED")
    except Exception as exc:
        run.status = "failed"
        run.error_code = str(exc) if isinstance(exc, SourceUnavailable) else type(exc).__name__
        run.error_message = (
            "来源连接或结构验证失败；未标记为无新增。"
            f"错误代码：{run.error_code}。"
        )
        if run.error_code in {
            "NANNING_ACCESS_RESTRICTED",
            "NANNING_RATE_LIMITED",
            "NANNING_COOLDOWN_ACTIVE",
        }:
            from .nanning import cooldown_until

            fallback_minutes = (
                settings.POLICY_CRAWL_FORBIDDEN_COOLDOWN_MINUTES
                if run.error_code == "NANNING_ACCESS_RESTRICTED"
                else settings.POLICY_CRAWL_RATE_LIMIT_COOLDOWN_MINUTES
            )
            resume_at = cooldown_until() or timezone.now() + timedelta(minutes=fallback_minutes)
            run.source.next_check_at = max(filter(None, [run.source.next_check_at, resume_at]))
            run.source.save(update_fields=["next_check_at"])
            run.error_message = (
                "官方来源返回访问限制或限流信号，系统已停止请求并进入冷却期；"
                "冷却结束后会从当前断点自动续采。"
            )
        if run.error_code == "NON_PUBLIC_ADDRESS":
            run.error_message = (
                "域名解析返回非公网地址；请检查 VPN 的 Fake-IP 与直连规则。"
                "网络恢复后可重新检查，本次未标记为无新增。"
            )
        if run.error_code in {"VPN_FAKE_IP_PROXY_REQUIRED", "VPN_PROXY_UNAVAILABLE"}:
            run.error_message = (
                "检测到 VPN Fake-IP，但本地代理当前不可用；系统会保留检索断点，"
                "VPN 关闭恢复公网 DNS 或代理恢复后可继续检查。"
            )
    run.finished_at = timezone.now()
    fields = ["status", "error_code", "error_message", "finished_at"]
    run.save(update_fields=fields)


@shared_task
def dispatch_imports():
    now = timezone.now()
    recover_interrupted_imports(expired_only=True, now=now)
    pending = (
        DiscoveredItem.objects.exclude(metadata={})
        .filter(Q(status="discovered") | Q(status="failed", attempts__lt=3, retry_at__lte=now))
        .order_by("created_at", "id")
    )
    selected = []
    nanning_busy = DiscoveredItem.objects.filter(
        source__adapter="nanning_v1", status="processing", lease_until__gt=now
    ).exists()
    for item in pending.select_related("source")[:50]:
        if item.source.adapter == "nanning_v1":
            if nanning_busy:
                continue
            nanning_busy = True
        selected.append(item.id)
        if len(selected) == 5:
            break
    for item_id in selected:
        if settings.LOCAL_WORKER:
            import_discovered(str(item_id))
        else:
            import_discovered.delay(str(item_id))


@shared_task
def import_discovered(item_id):
    with transaction.atomic():
        item = DiscoveredItem.objects.select_for_update().select_related("source").get(pk=item_id)
        now = timezone.now()
        if item.status not in {"discovered", "failed"} or not item.metadata:
            return
        if item.status == "failed" and (
            item.attempts >= 3 or not item.retry_at or item.retry_at > now
        ):
            return
        lease = now + timedelta(minutes=settings.POLICY_IMPORT_LEASE_MINUTES)
        item.status, item.lease_until = "processing", lease
        item.attempts += 1
        item.error_code = ""
        item.error_detail = ""
        item.save(
            update_fields=["status", "lease_until", "attempts", "error_code", "error_detail"]
        )
    try:
        if item.source.adapter == "nanning_v1":
            from .nanning_importer import import_record as import_nanning_record

            import_nanning_record(item.metadata, item.source, claim=(item.pk, lease))
        else:
            import_record(item.metadata, item.source, claim=(item.pk, lease))
    except Exception as exc:
        code = str(exc) if isinstance(exc, SourceUnavailable) else type(exc).__name__
        cooldown_codes = {
            "NANNING_ACCESS_RESTRICTED",
            "NANNING_RATE_LIMITED",
            "NANNING_COOLDOWN_ACTIVE",
        }
        network_route_codes = {
            "VPN_FAKE_IP_PROXY_REQUIRED",
            "VPN_PROXY_UNAVAILABLE",
        }
        retry_at = timezone.now() + timedelta(minutes=5 * item.attempts)
        updates = {
            "status": "failed",
            "error_code": code[:100],
            "error_detail": str(exc)[:500],
            "lease_until": None,
            "retry_at": retry_at,
        }
        if code in cooldown_codes:
            from .nanning import cooldown_until

            fallback_minutes = (
                settings.POLICY_CRAWL_FORBIDDEN_COOLDOWN_MINUTES
                if code == "NANNING_ACCESS_RESTRICTED"
                else settings.POLICY_CRAWL_RATE_LIMIT_COOLDOWN_MINUTES
            )
            updates["retry_at"] = cooldown_until() or timezone.now() + timedelta(
                minutes=fallback_minutes
            )
            updates["attempts"] = F("attempts") - 1
            Source.objects.filter(pk=item.source_id).update(next_check_at=updates["retry_at"])
        elif code in network_route_codes:
            # Starting/stopping the local VPN is an environment change, not a bad
            # policy link. Keep it retryable without exhausting the link's budget.
            updates["retry_at"] = timezone.now() + timedelta(minutes=1)
            updates["attempts"] = F("attempts") - 1
        DiscoveredItem.objects.filter(pk=item.pk, status="processing", lease_until=lease).update(
            **updates
        )


def recover_interrupted_imports(*, expired_only=False, now=None):
    """Requeue work interrupted by a worker restart without reporting a parse failure."""
    now = now or timezone.now()
    items = DiscoveredItem.objects.filter(status="processing")
    if expired_only:
        items = items.filter(lease_until__lte=now)
    return items.update(
        status="discovered",
        attempts=0,
        error_code="",
        error_detail="",
        lease_until=None,
        retry_at=None,
    )
