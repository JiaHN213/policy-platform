import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from .models import KnowledgeBuild
from .services import knowledge_fingerprint, sync_all

logger = logging.getLogger(__name__)


@shared_task(soft_time_limit=540, time_limit=600)
def repair_relation(job_id):
    from .repair import process

    process(job_id)


@shared_task(soft_time_limit=540, time_limit=600)
def refresh_relation_pages(job_id):
    from .repair import refresh

    refresh(job_id)


@shared_task(soft_time_limit=540, time_limit=600)
def dispatch_relation_repairs():
    from .models import RelationRepair
    from .repair import enabled

    if not enabled():
        return
    now = timezone.now()
    job = (RelationRepair.objects.filter(
        Q(status="queued") | Q(status="running", lease_until__lte=now))
        .filter(Q(retry_at__isnull=True) | Q(retry_at__lte=now))
        .order_by("created_at").first())
    if job:
        # A lease and attempt token in process prevent duplicate delivery effects.
        repair_relation.delay(str(job.pk))
    pending_refresh = RelationRepair.objects.filter(
        status="succeeded", result__page_refresh__in=["pending", "running"]
    ).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now)).filter(
        Q(retry_at__isnull=True) | Q(retry_at__lte=now)).order_by("updated_at").first()
    if pending_refresh:
        refresh_relation_pages.delay(str(pending_refresh.pk))


def enqueue_sync(requested_by=None, force=False):
    digest = knowledge_fingerprint()
    with transaction.atomic():
        if connection.vendor == "postgresql":
            # Serialize the empty-queue case too: concurrent events share one build.
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(76241002)")
        active = (
            KnowledgeBuild.objects.select_for_update()
            .filter(status__in=["queued", "running"])
            .order_by("created_at")
            .first()
        )
        if active:
            return active, False
        latest = KnowledgeBuild.objects.filter(status="succeeded").order_by("-created_at").first()
        if (not force and latest and latest.input_hash == digest
                and not latest.result.get("failed_pages")
                and not (latest.result.get("lint") or {}).get("issues", 0)
                and not (latest.result.get("relation_audit") or {}).get("pending", 0)):
            return latest, False
        return (
            KnowledgeBuild.objects.create(
                kind="sync",
                input_hash=digest,
                requested_by=requested_by,
            ),
            True,
        )


def process_build(build_id):
    with transaction.atomic():
        build = KnowledgeBuild.objects.select_for_update().get(pk=build_id)
        now = timezone.now()
        if build.status == "succeeded" or (build.lease_until and build.lease_until > now):
            return
        build.status = "running"
        build.attempts += 1
        build.lease_until = now + timedelta(minutes=90)
        build.error_code = ""
        build.save()
        attempt = build.attempts
    try:
        result = sync_all()
        with transaction.atomic():
            updated = KnowledgeBuild.objects.filter(
                pk=build_id, attempts=attempt, status="running"
            ).update(
                status="succeeded",
                result=result,
                input_hash=result["input_hash"],
                error_code="",
                lease_until=None,
            )
            pending = result.get("relation_audit", {}).get("pending", 0)
            if updated and pending and not KnowledgeBuild.objects.filter(
                status__in=["queued", "running"]
            ).exists():
                KnowledgeBuild.objects.create(
                    kind="sync",
                    input_hash=result["input_hash"],
                    requested_by=build.requested_by,
                    result={"continuation_of": str(build_id), "pending_relation_scans": pending},
                )
    except Exception:
        logger.exception("Wiki build %s failed", build_id)
        KnowledgeBuild.objects.filter(pk=build_id, attempts=attempt, status="running").update(
            status="failed", error_code="WIKI_BUILD_FAILED", lease_until=None
        )


@shared_task(soft_time_limit=5100, time_limit=5400)
def build_knowledge(build_id):
    process_build(build_id)


@shared_task
def dispatch_knowledge_builds():
    now = timezone.now()
    KnowledgeBuild.objects.filter(attempts__gte=3).filter(
        Q(status="running", lease_until__lte=now) | Q(status="queued")
    ).update(
        status="failed", lease_until=None, error_code="WIKI_BUILD_FAILED",
    )
    enqueue_sync()
    build = (
        KnowledgeBuild.objects.filter(attempts__lt=3)
        .filter(Q(status__in=["queued", "failed"]) | Q(status="running", lease_until__lte=now))
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
        .order_by("created_at")
        .first()
    )
    if not build:
        return
    if settings.LOCAL_WORKER:
        process_build(build.pk)
    else:
        build_knowledge.delay(str(build.pk))
