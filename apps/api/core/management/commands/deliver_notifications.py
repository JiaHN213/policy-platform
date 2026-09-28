from django.core.management.base import BaseCommand
from subscriptions.tasks import deliver_pending


class Command(BaseCommand):
    help = "同步处理待发送的站内通知（开发联调，无外部消息发送）。"

    def handle(self, *args, **options):
        deliver_pending()
        self.stdout.write("站内通知事件处理完成。")
