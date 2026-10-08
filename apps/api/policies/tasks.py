from celery import shared_task
from core.ai_runtime import get_ai_profile
from django.db.models import Q
from django.utils import timezone

from .enrichment import configured, eligible, process_one
from .models import AIReviewControl, PolicyEnrichment


@shared_task(soft_time_limit=1500, time_limit=1560)
def recover_review(record_id):
    from .recovery import process
    process(record_id)


@shared_task
def dispatch_review_recovery():
    from .models import ReviewRecovery
    from .recovery import automatic_candidates, enqueue

    control, _ = AIReviewControl.objects.get_or_create(singleton_key="default")
    if control.enabled and control.recovery_enabled:
        for job in automatic_candidates().select_related("policy")[:10]:
            try:
                enqueue(job, automatic=True)
            except ValueError:
                continue
    now = timezone.now()
    if ReviewRecovery.objects.filter(status="running", lease_until__gt=now).exists():
        return
    candidates = ReviewRecovery.objects.filter(
        Q(status="queued") | Q(status="running", lease_until__lte=now) |
        Q(status="failed", automatic=True, attempts__lt=control.recovery_attempt_limit)
    ).filter(Q(retry_at__isnull=True) | Q(retry_at__lte=now)).order_by("created_at")
    if not control.enabled or not control.recovery_enabled:
        candidates = candidates.filter(automatic=False)
    record = candidates.first()
    if record:
        ReviewRecovery.objects.filter(pk=record.pk, status="failed").update(status="queued")
        recover_review.delay(str(record.pk))


def ensure_enrichment_jobs():
    """Create current-version jobs without starting model work."""
    from django.db.models import Exists, OuterRef

    pending = (
        eligible()
        .annotate(
            has_job=Exists(
                PolicyEnrichment.objects.filter(
                    policy_id=OuterRef("pk"), policy_version=OuterRef("version")
                )
            )
        )
        .filter(has_job=False)
    )
    for policy in pending.order_by("created_at", "id")[:20]:
        PolicyEnrichment.objects.get_or_create(
            policy=policy,
            policy_version=policy.version,
            defaults={"prompt_version": "policy-enrichment-v6"},
        )


def runnable_enrichment_jobs():
    now = timezone.now()
    return (
        PolicyEnrichment.objects.exclude(status="succeeded")
        .filter(recovery_token__isnull=True)
        .filter(attempts__lt=3)
        .filter(Q(retry_at__isnull=True) | Q(retry_at__lte=now))
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
        .order_by("created_at", "id")
    )


@shared_task(soft_time_limit=1500, time_limit=1560)
def enrich_policy(job_id):
    process_one(job_id)


@shared_task
def dispatch_enrichment():
    from django.conf import settings

    if (
        not configured()
        or not AIReviewControl.objects.filter(singleton_key="default", enabled=True).exists()
    ):
        return
    ensure_enrichment_jobs()
    now = timezone.now()
    jobs = runnable_enrichment_jobs()
    capacity = get_ai_profile("review").concurrency - PolicyEnrichment.objects.filter(
        status="running", lease_until__gt=now
    ).count()
    if capacity <= 0:
        return
    for job in jobs[:capacity]:
        if settings.LOCAL_WORKER:
            process_one(job.pk)
        else:
            enrich_policy.delay(str(job.pk))
