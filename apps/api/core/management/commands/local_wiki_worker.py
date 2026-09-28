import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone
from knowledge.models import KnowledgeBuild
from knowledge.tasks import dispatch_knowledge_builds


class Command(BaseCommand):
    help = "本地 Wiki LLM 增量构建执行器；按配置频率合并处理新发布政策。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if not settings.LOCAL_WORKER:
            raise CommandError("仅限本地开发环境；部署使用 Celery。")

        # A manual rebuild runner may own a live lease. A worker restart must not
        # release that lease and execute the same build concurrently.
        KnowledgeBuild.objects.filter(status="running").filter(
            Q(lease_until__lte=timezone.now()) | Q(lease_until__isnull=True)
        ).update(
            status="queued", lease_until=None
        )
        interval = settings.KNOWLEDGE_SYNC_INTERVAL_MINUTES * 60
        self.stdout.write(
            f"Wiki LLM worker started; incremental build interval is "
            f"{settings.KNOWLEDGE_SYNC_INTERVAL_MINUTES} minutes."
        )
        if options["once"]:
            dispatch_knowledge_builds()
            return

        next_sync = time.monotonic() + interval
        while True:
            close_old_connections()
            if time.monotonic() >= next_sync:
                dispatch_knowledge_builds()
                next_sync = time.monotonic() + interval
            time.sleep(15)
