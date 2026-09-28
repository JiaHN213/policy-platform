import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from policies.enrichment import configured, process_one
from policies.models import AIReviewControl
from policies.tasks import ensure_enrichment_jobs, runnable_enrichment_jobs

from core.ai_runtime import get_ai_profile


def process_with_connection_cleanup(job_id):
    close_old_connections()
    try:
        process_one(job_id)
    finally:
        close_old_connections()


class Command(BaseCommand):
    help = "独立本地 AI 执行器，避免模型调用阻塞采集和推送。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if not settings.LOCAL_WORKER:
            raise CommandError("仅限本地开发环境；部署使用 Celery。")
        max_concurrency = 4
        self.stdout.write("Local AI worker started; concurrency follows the review setting.")
        futures = {}
        with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            while True:
                close_old_connections()
                for future in list(futures):
                    if not future.done():
                        continue
                    job_id = futures.pop(future)
                    try:
                        future.result()
                    except Exception as exc:
                        self.stderr.write(f"AI review task {job_id} failed: {exc}")

                enabled = configured() and AIReviewControl.objects.filter(
                    singleton_key="default", enabled=True
                ).exists()
                if enabled:
                    ensure_enrichment_jobs()
                    concurrency = get_ai_profile("review").concurrency
                    capacity = concurrency - len(futures)
                    if capacity > 0:
                        active_ids = list(futures.values())
                        jobs = runnable_enrichment_jobs().exclude(pk__in=active_ids)[:capacity]
                        for job_id in jobs.values_list("id", flat=True):
                            futures[executor.submit(process_with_connection_cleanup, job_id)] = job_id

                if options["once"]:
                    for future in futures:
                        future.result()
                    return
                time.sleep(2)
