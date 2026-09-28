from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from policies.models import Policy
from policies.services import fingerprint


class Command(BaseCommand):
    help = "仅开发环境创建明确标注的虚构待审核样例。不会发布，也不会创建用户。"

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("演示数据仅允许在 DEBUG=true 的开发环境创建。")
        examples = [
            (
                "【演示】城市供水设施数字化改造",
                ["水务", "人工智能＋"],
                "本记录仅用于测试政策审核和订阅流程，不是真实政策。演示内容：供水设施监测、漏损分析与设备数字化。",
            ),
            (
                "【演示】污水处理与生态环境监测",
                ["水务", "环保"],
                "本记录为虚构测试样例，不构成政策依据。演示内容：污水治理、环境监测与水资源保护。",
            ),
            (
                "【演示】人工智能产业应用研究",
                ["人工智能＋"],
                "本记录为虚构测试样例。仅用于测试人工智能主题的搜索、订阅和人工审核。",
            ),
        ]
        for title, topics, body in examples:
            Policy.objects.get_or_create(
                source_key=fingerprint(title),
                defaults={
                    "title": title,
                    "topics": topics,
                    "body": body,
                    "issuer": "演示数据 · 非政府文件",
                    "publication_date": date(2026, 9, 15),
                    "source_url": "https://example.invalid/demo",
                    "content_hash": fingerprint(body),
                    "is_demo": True,
                },
            )
        self.stdout.write("已创建3条虚构待审核样例。请先创建订阅，再在审核页发布以验证通知。")
