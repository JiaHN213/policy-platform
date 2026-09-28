from django.conf import settings
from django.core.management.base import BaseCommand
from langgraph.checkpoint.postgres import PostgresSaver


class Command(BaseCommand):
    help = "初始化 LangGraph PostgreSQL checkpoint 表；在部署迁移时运行。"

    def handle(self, *args, **options):
        with PostgresSaver.from_conn_string(settings.CHECKPOINT_DATABASE_URL) as saver:
            saver.setup()
        self.stdout.write("Checkpoint 表已初始化。")
