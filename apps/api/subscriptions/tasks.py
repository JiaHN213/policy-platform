from celery import shared_task
from policies.event_tasks import dispatch_publication_consumers

from .services import create_deadline_reminders


@shared_task
def deliver_pending():
    return dispatch_publication_consumers(consumer="subscription")


@shared_task
def deliver_deadline_reminders():
    return create_deadline_reminders()
