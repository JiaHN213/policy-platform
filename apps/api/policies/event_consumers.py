"""Durable, independently retryable publication consumers. No model work on publication."""

import logging
import uuid
from datetime import timedelta

from core.errors import Conflict
from core.models import AuditRecord
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import PublicationConsumption as Consumption
from .models import PublicationEvent

logger = logging.getLogger(__name__)
MAX_FAILURES = 5


def seed_consumers(event):
    consumers = list(Consumption.Consumer.values)
    if event.kind.startswith("opportunity.deadline."):
        consumers = ["subscription", "statistics"]
    for consumer in consumers:
        done = consumer == "subscription" and event.delivered_at is not None
        Consumption.objects.get_or_create(
            event=event,
            consumer=consumer,
            defaults={
                "status": "succeeded" if done else "pending",
                "succeeded_at": event.delivered_at if done else None,
                "last_success_version": event.policy_version if done else None,
                "retry_at": event.created_at
                + timedelta(minutes=settings.KNOWLEDGE_SYNC_INTERVAL_MINUTES)
                if consumer == "wiki"
                else None,
                "result": {"message": "原有通知已处理，保留处理结果，不重复发送。"} if done else {},
            },
        )


def due_consumptions(consumer):
    now = timezone.now()
    return (
        Consumption.objects.filter(consumer=consumer)
        .filter(
            Q(status__in=["pending", "retry", "waiting"])
            | Q(status="running", lease_until__lte=now)
        )
        .filter(Q(retry_at__isnull=True) | Q(retry_at__lte=now))
    )


def _wiki(job):
    from knowledge.models import KnowledgeBuild
    from knowledge.tasks import enqueue_sync

    build = job.wiki_build
    if build and build.status == "failed":
        raise RuntimeError("Wiki 构建失败，请检查 Wiki 模型连接和构建记录后重试。")
    if build and build.status == "succeeded":
        if build.result.get("failed_pages"):
            raise RuntimeError("部分知识页未通过模型引用或结构校验；旧知识页已保留，需重新构建。")
        if (build.result.get("lint") or {}).get("issues", 0):
            raise RuntimeError("知识页存在过期引用或证据问题，需要重新构建并校验。")
        if build.created_at >= job.event.created_at and not (
            build.result.get("relation_audit") or {}
        ).get("pending", 0):
            return (
                {"message": "知识页与本轮关系扫描已完成。", "build_id": str(build.pk)},
                job.event.policy_version,
                None,
            )
        build = None
    if not build:
        # A successful periodic build can satisfy multiple events without rebuilding.
        build = (
            KnowledgeBuild.objects.filter(status="succeeded", created_at__gte=job.event.created_at)
            .order_by("-created_at")
            .first()
        )
        if (
            build
            and not build.result.get("failed_pages")
            and not (build.result.get("lint") or {}).get("issues", 0)
            and not (build.result.get("relation_audit") or {}).get("pending", 0)
        ):
            return (
                {"message": "已由定期合并构建完成。", "build_id": str(build.pk)},
                job.event.policy_version,
                None,
            )
        build, _ = enqueue_sync(force=True)
    return (
        {"message": "已合并到 Wiki 构建任务，等待构建及关系扫描完成。", "build_id": str(build.pk)},
        None,
        build,
    )


def _handle(job):
    event = job.event
    if job.consumer == "subscription":
        from subscriptions.services import deliver_event

        count = deliver_event(event.pk)
        return (
            {
                "message": f"订阅匹配完成，本次新增 {count} 条站内通知。",
                "notifications_created": count,
            },
            event.policy_version,
            None,
        )
    if job.consumer == "search":
        from .opensearch import sync_policy

        return sync_policy(event.policy_id), None, None
    if job.consumer == "wiki":
        return _wiki(job)
    # Unique consumer ownership plus the event lock make audit compensation idempotent.
    with transaction.atomic():
        PublicationEvent.objects.select_for_update().get(pk=event.pk)
        AuditRecord.objects.get_or_create(
            action="publication.statistics",
            object_id=event.pk,
            defaults={
                "details": {
                    "policy_id": str(event.policy_id),
                    "version": event.policy_version,
                    "kind": event.kind,
                }
            },
        )
    return {"message": "事件已计入统计审计；重复处理不会重复计数。"}, event.policy_version, None


def process_consumption(job_id):
    with transaction.atomic():
        job = Consumption.objects.select_for_update().get(pk=job_id)
        now = timezone.now()
        if (
            job.status in {"succeeded", "failed"}
            or (job.lease_until and job.lease_until > now)
            or (job.retry_at and job.retry_at > now)
        ):
            return
        if job.status != "waiting":
            job.attempts += 1
        job.status = "running"
        job.lease_token = uuid.uuid4()
        job.lease_until = now + timedelta(minutes=10)
        job.save()
        token = job.lease_token
    try:
        result, version, build = _handle(job)
        Consumption.objects.filter(pk=job.pk, lease_token=token).update(
            status="waiting" if build else "succeeded",
            wiki_build=build,
            result=result,
            last_error="",
            last_success_version=version if version is not None else result.get("version"),
            succeeded_at=None if build else timezone.now(),
            retry_at=timezone.now() + timedelta(seconds=60) if build else None,
            lease_until=None,
            lease_token=None,
            updated_at=timezone.now(),
        )
    except Exception as exc:
        logger.exception("Publication consumer failed: %s / %s", job.pk, job.consumer)
        # Never expose raw provider responses, credentials, or internal exception codes.
        reason = {
            "search": "搜索服务未启用、连接失败或索引写入未完成；请检查搜索服务后重试。数据库检索仍可使用。",
            "subscription": "订阅匹配或站内通知写入失败；请检查数据库服务后重试。已有通知不会重复发送。",
            "wiki": str(exc)
            if type(exc) is RuntimeError
            and str(exc).startswith(("Wiki 构建失败", "部分知识页", "知识页存在"))
            else "Wiki 任务创建或构建失败；请检查模型连接和知识构建记录后重试。",
            "statistics": "统计审计记录写入失败；请检查数据库连接后重试。",
        }[job.consumer]
        failures = job.failures + 1
        Consumption.objects.filter(pk=job.pk, lease_token=token).update(
            status="failed" if failures >= MAX_FAILURES else "retry",
            failures=failures,
            last_error=reason,
            wiki_build=None,
            retry_at=None if failures >= MAX_FAILURES else timezone.now() + timedelta(seconds=60 * 2 ** (failures - 1)),
            lease_until=None,
            lease_token=None,
            updated_at=timezone.now(),
        )


@transaction.atomic
def retry_consumption(job_id, actor):
    job = Consumption.objects.select_for_update().get(pk=job_id)
    if job.status not in {"retry", "failed"}:
        raise Conflict("仅失败或等待重试的环节可以重试；正在处理和已完成的环节无需重复提交。")
    job.status = "pending"
    job.failures = 0
    job.retry_at = None
    job.lease_until = None
    job.lease_token = None
    job.save()
    AuditRecord.objects.create(
        actor=actor,
        action="publication.consumer.retry",
        object_id=job.pk,
        details={"consumer": job.consumer, "event_id": str(job.event_id)},
    )
    return job
