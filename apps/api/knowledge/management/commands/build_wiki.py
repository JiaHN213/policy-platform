from django.core.management.base import BaseCommand

from knowledge.services import sync_all


class Command(BaseCommand):
    help = "从当前正式政策、AI 摘要和政策关系构建可追溯的知识库。"

    def handle(self, *args, **options):
        result = sync_all()
        self.stdout.write(self.style.SUCCESS(f"知识库构建完成：{result}"))
