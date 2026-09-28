from django.core.management.base import BaseCommand
from django.utils import timezone
from policies.extraction import extract_metadata
from policies.models import Policy


class Command(BaseCommand):
    help = "从现有正文生成原文摘要与结构化关键词；不推断状态或政策关系。"

    def handle(self, *args, **options):
        count = 0
        for policy in Policy.objects.all().iterator(chunk_size=100):
            if policy.extraction_version == policy.version:
                continue
            count += Policy.objects.filter(pk=policy.pk, version=policy.version).update(
                **extract_metadata(policy.body),
                extraction_version=policy.version,
                updated_at=timezone.now(),
            )
        self.stdout.write(f"Extracted metadata for {count} policies.")
