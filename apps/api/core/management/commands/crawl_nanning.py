import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from ingestion.models import Source
from ingestion.nanning import TERMS, URL
from ingestion.tasks import check_source, queue_check


class Command(BaseCommand):
    help = "南宁政策库全量列表和水务全文召回；支持已有未完成任务续跑。正文由导入执行器处理。"

    def add_arguments(self, parser):
        parser.add_argument("--slices", type=int, default=0)

    def handle(self, *args, **options):
        if not settings.LOCAL_WORKER:
            raise CommandError("本命令供本地全量初始化；服务器使用计划任务执行器。")
        source, _ = Source.objects.get_or_create(
            url=URL,
            defaults={
                "name": "南宁市政策文件库",
                "adapter": "nanning_v1",
                "enabled": True,
                "interval_minutes": 1440,
                "verification_status": "verified",
                "notes": "完整列表按日期分区采集；水务环保正文筛选，技术与政策方向独立标签。",
            },
        )
        if (
            source.runs.filter(error_code="NANNING_ACCESS_RESTRICTED").exists()
            and not source.enabled
        ):
            raise CommandError(
                "来源因官方访问限制已暂停。请先联系来源方恢复访问，确认后在来源管理启用，再续跑。"
            )
        run = queue_check(source.pk)
        if run.progress and run.progress.get("query_index") == 0 and run.status == "queued":
            run.progress["queries"] = list(dict.fromkeys(run.progress["queries"] + TERMS))
            run.save(update_fields=["progress"])
        slices = 0
        self.stdout.write(f"Nanning run: {run.pk}")
        while True:
            close_old_connections()
            run.refresh_from_db()
            if run.status in {"succeeded", "failed", "partial"}:
                self.stdout.write(f"Run {run.status}; {run.error_code}; {run.progress}")
                return
            if run.status == "queued":
                check_source(str(run.pk))
                slices += 1
                run.refresh_from_db()
                p = run.progress
                self.stdout.write(
                    f"pages={p.get('pages_scanned', 0)} catalog={p.get('catalog_rows', 0)}/{p.get('catalog_total')} rows={p.get('rows_scanned', 0)} stage={p.get('query_index', 0)} status={run.status}"
                )
                self.stdout.flush()
                if options["slices"] and slices >= options["slices"]:
                    return
            time.sleep(0.2)
