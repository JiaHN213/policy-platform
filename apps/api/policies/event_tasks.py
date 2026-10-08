from celery import shared_task
from django.conf import settings

from .event_consumers import due_consumptions, process_consumption
from .models import PublicationConsumption


@shared_task(soft_time_limit=540, time_limit=600)
def consume_publication(job_id):
    process_consumption(job_id)


@shared_task
def consume_notification(job_id):
    process_consumption(job_id)


@shared_task
def dispatch_publication_consumers(consumer=None):
    consumers = [consumer] if consumer else PublicationConsumption.Consumer.values
    for name in consumers:
        for job_id in list(due_consumptions(name).values_list("id", flat=True)[:30]):
            if settings.LOCAL_WORKER:
                process_consumption(job_id)
            else:
                task = consume_notification if name == "subscription" else consume_publication
                task.delay(str(job_id))
