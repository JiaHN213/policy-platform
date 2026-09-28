from django.core.management.base import BaseCommand
from ingestion.models import Source
from ingestion.nanning import URL as NANNING_LIBRARY_URL


class Command(BaseCommand):
    help = "在空数据库中登记南宁市政策文件库；保留已有来源配置，不创建默认密码。"

    def handle(self, *args, **options):
        Source.objects.get_or_create(
            url=NANNING_LIBRARY_URL,
            defaults={
                "name": "南宁市政策文件库",
                "adapter": "nanning_v1",
                "enabled": True,
                "interval_minutes": 1440,
                "verification_status": "verified",
                "notes": "完整列表按日期分区采集；水务环保正文筛选，技术与政策方向独立标签。",
            },
        )
        self.stdout.write(
            self.style.SUCCESS("南宁市政策文件库已登记。使用 createsuperuser 创建你的本地管理员。")
        )
