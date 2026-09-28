"""Import one live policy from the first two public department/council pages."""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from ingestion.gov_library import GOV_LIBRARY_URL, fetch_list_page
from ingestion.importer import import_record
from ingestion.models import Source


class Command(BaseCommand):
    help = "开发联调：从官方列表验证指定 URL，保存正文/PDF 原件并创建待审核候选，绝不自动发布。"

    def add_arguments(self, parser):
        parser.add_argument("--url", required=True)

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Only available in the development environment")
        record = None
        for category in ("bm", "gw"):
            for page in (1, 2):
                result = fetch_list_page(category, page)
                record = next((r for r in result["items"] if r["url"] == options["url"]), None)
                if record:
                    break
                time.sleep(1)
            if record:
                break
        if not record:
            raise CommandError("URL was not found in the verified sample window")
        source, _ = Source.objects.get_or_create(
            url=GOV_LIBRARY_URL,
            defaults={
                "name": "中国政府网 · 国务院政策文件库",
                "adapter": "gov_library_html_v1",
                "enabled": False,
                "notes": "开发联调用的政策来源，需单独验证后启用定时采集。",
            },
        )
        policy, created, snapshots, evidence, chars = import_record(record, source)
        self.stdout.write(
            f"{'Created' if created else 'Unchanged'} candidate {policy.id}; "
            f"originals={snapshots}, evidence={evidence}, chars={chars}"
        )
