from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from policies.event_consumers import seed_consumers
from policies.models import PublicationEvent


class Command(BaseCommand):
    help = "补齐发布事件缺失的处理记录；保留完成状态，不重发已经处理的通知。"

    def handle(self, *args, **options):
        events = PublicationEvent.objects.annotate(consumers_count=Count("consumptions")).filter(
            Q(kind__startswith="opportunity.deadline.", consumers_count__lt=2)
            | (~Q(kind__startswith="opportunity.deadline.") & Q(consumers_count__lt=4))
        )
        count = 0
        for event in events.iterator(chunk_size=200):
            seed_consumers(event)
            count += 1
        self.stdout.write(f"Repaired consumer records for {count} event(s).")
