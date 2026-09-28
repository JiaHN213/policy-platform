import json

from django.core.management.base import BaseCommand, CommandError

from policies.opensearch import OpenSearchUnavailable, sync_index


class Command(BaseCommand):
    help = "同步正式政策到 OpenSearch；--rebuild 会重建索引。"

    def add_arguments(self, parser):
        parser.add_argument("--rebuild", action="store_true")

    def handle(self, *args, **options):
        try:
            result = sync_index(rebuild=options["rebuild"])
        except OpenSearchUnavailable as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(json.dumps(result, ensure_ascii=False, default=str)))

