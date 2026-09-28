from celery import shared_task

from .services import evaluate


@shared_task
def evaluate_daily():
    run = evaluate(scheduled=True)
    return str(run.pk) if run else None
