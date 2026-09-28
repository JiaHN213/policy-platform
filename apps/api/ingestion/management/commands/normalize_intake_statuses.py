from django.core.management.base import BaseCommand
from django.db import transaction

from ingestion.models import DiscoveredItem


class Command(BaseCommand):
    help = "Move false intake-review backlog to excluded or indexed states."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist changes. Without this option the command only reports counts.",
        )

    def handle(self, *args, **options):
        pending = DiscoveredItem.objects.filter(status="needs_review", policy__isnull=True)
        excluded = pending.filter(metadata__scope_assessment__decision="excluded")
        weak_matches = pending.filter(metadata__scope_assessment__decision="needs_review")
        counts = {
            "excluded": excluded.count(),
            "indexed": weak_matches.count(),
            "remaining": pending.exclude(
                metadata__scope_assessment__decision__in=["excluded", "needs_review"]
            ).count(),
        }
        self.stdout.write(
            "would move {excluded} to excluded, {indexed} to indexed; "
            "{remaining} records remain for source metadata review".format(**counts)
        )
        if not options["apply"]:
            self.stdout.write("dry run only; pass --apply to persist")
            return
        with transaction.atomic():
            common = {
                "error_code": "",
                "error_detail": "",
                "retry_at": None,
                "lease_until": None,
            }
            moved_excluded = excluded.update(status="excluded", **common)
            moved_indexed = weak_matches.update(status="indexed", **common)
        self.stdout.write(
            self.style.SUCCESS(
                f"moved {moved_excluded} to excluded and {moved_indexed} to indexed"
            )
        )
