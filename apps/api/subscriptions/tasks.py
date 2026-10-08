from celery import shared_task
from policies.event_tasks import dispatch_publication_consumers

from .delivery import deadline_reminders, deliver_digests
from .following import sync_pending_follows


@shared_task
def deliver_pending():
    return dispatch_publication_consumers(consumer="subscription")


@shared_task
def deliver_deadline_reminders():
    return deadline_reminders()


@shared_task
def maintain_subscriptions():
    return {"synced": sync_pending_follows(), "digests": deliver_digests()}
