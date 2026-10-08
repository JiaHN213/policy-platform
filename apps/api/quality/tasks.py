from celery import shared_task

from .matching import record_observation
from .services import evaluate


@shared_task
def evaluate_daily():
    run = evaluate(scheduled=True)
    return str(run.pk) if run else None


@shared_task
def observe_matching():
    return record_observation()
