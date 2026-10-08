from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from .models import WatchRun
from .watching import process_batch, prune_history, start_due_runs


@shared_task(soft_time_limit=570, time_limit=600)
def process_watch(run_id):
    process_batch(run_id)


@shared_task
def dispatch_watches():
    start_due_runs()
    now = timezone.now()
    # At most one background recommendation batch at a time. Model requests also
    # share the existing endpoint capacity; recommendations do not get priority.
    if WatchRun.objects.filter(status="running", lease_until__gt=now).exists():
        return
    run = (
        WatchRun.objects.filter(status__in=["queued", "running"])
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
        .filter(Q(retry_at__isnull=True) | Q(retry_at__lte=now))
        .order_by("updated_at", "pk")
        .first()
    )
    if run:
        process_watch.delay(str(run.pk))


@shared_task
def maintain_watch_history():
    return prune_history()
