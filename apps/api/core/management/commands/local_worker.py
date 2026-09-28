import logging
import time
from threading import Event, Thread

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from ingestion.models import SourceCheckRun
from ingestion.tasks import (
    check_source,
    dispatch_due_sources,
    dispatch_imports,
    recover_interrupted_imports,
)
from policies.event_tasks import dispatch_publication_consumers
from policies.models import PublicationConsumption
from policies.search_tasks import sync_opensearch
from subscriptions.tasks import deliver_deadline_reminders

logger = logging.getLogger(__name__)


def run_event_consumer(stop_event, consumer):
    while not stop_event.is_set():
        close_old_connections()
        try:
            dispatch_publication_consumers(consumer=consumer)
        except Exception:
            logger.exception("Publication consumer dispatcher failed: %s", consumer)
        finally:
            close_old_connections()
        stop_event.wait(15)


def run_quality_evaluation(stop_event):
    from quality.tasks import evaluate_daily

    while not stop_event.is_set():
        close_old_connections()
        try:
            evaluate_daily()
        except Exception:
            logger.exception("Quality evaluation failed")
        finally:
            close_old_connections()
        stop_event.wait(3600)


def run_search_sync(stop_event):
    """Keep search indexing independent from slow crawl/import work."""
    while not stop_event.is_set():
        close_old_connections()
        try:
            sync_opensearch()
        except Exception:
            logger.exception("Unexpected OpenSearch synchronization failure")
        finally:
            close_old_connections()
        stop_event.wait(60)


class Command(BaseCommand):
    help = "Windows 开发用轮询执行器；部署时使用 Celery Worker/Beat。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if not settings.LOCAL_WORKER:
            raise CommandError("仅限 DEBUG=true 且 LOCAL_WORKER=true 的本地环境。")
        recovered = recover_interrupted_imports()
        if recovered:
            self.stdout.write(f"Recovered {recovered} interrupted policy import(s).")
        self.stdout.write("Local development worker started (no Redis).")
        stop_search_sync = Event()
        search_thread = None
        consumer_threads = []
        if options["once"]:
            sync_opensearch()
            dispatch_publication_consumers()
        else:
            search_thread = Thread(
                target=run_search_sync,
                args=(stop_search_sync,),
                name="local-search-sync",
                daemon=True,
            )
            search_thread.start()
            quality_thread = Thread(target=run_quality_evaluation, args=(stop_search_sync,), name="local-quality-evaluation", daemon=True)
            quality_thread.start()
            consumer_threads.append(quality_thread)
            for consumer in PublicationConsumption.Consumer.values:
                thread = Thread(target=run_event_consumer, args=(stop_search_sync, consumer),
                                name=f"local-publication-{consumer}", daemon=True)
                thread.start()
                consumer_threads.append(thread)
        try:
            last_deadline_check = 0.0
            while True:
                close_old_connections()
                dispatch_due_sources()
                for run_id in SourceCheckRun.objects.filter(status="queued").values_list(
                    "id", flat=True
                )[:10]:
                    check_source(str(run_id))
                dispatch_imports()
                if time.monotonic() - last_deadline_check >= 300:
                    deliver_deadline_reminders()
                    last_deadline_check = time.monotonic()
                if options["once"]:
                    break
                time.sleep(5)
        finally:
            if search_thread is not None:
                stop_search_sync.set()
                search_thread.join(timeout=5)
                for thread in consumer_threads:
                    thread.join(timeout=5)
