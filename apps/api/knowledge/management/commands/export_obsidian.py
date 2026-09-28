from django.core.management.base import BaseCommand

from knowledge.obsidian import export_vault


class Command(BaseCommand):
    help = "将知识页导出为中文 Obsidian Vault，并支持受控的政策内容修改。"

    def add_arguments(self, parser):
        parser.add_argument("--output", help="导出目录；默认使用 OBSIDIAN_EXPORT_DIR。")

    def handle(self, *args, **options):
        result = export_vault(options.get("output"))
        self.stdout.write(self.style.SUCCESS(f"Obsidian Vault 导出完成：{result}"))
