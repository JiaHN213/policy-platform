from copy import deepcopy

from core.business_config import checksum, clear_config_cache, validate_documents
from core.models import SystemConfigAudit, SystemConfigDocument, SystemConfigRelease
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = "发布Wiki关系修正规则的新配置版本，保留其他配置及历史版本。"

    @transaction.atomic
    def handle(self, *args, **options):
        source = SystemConfigRelease.objects.select_for_update().filter(status="published").first()
        if not source:
            self.stdout.write("没有数据库配置版本，使用已更新的文件基线。")
            return
        documents = {d.key: deepcopy(d.content) for d in source.documents.all()}
        before = deepcopy(documents["wiki_relations"])
        rules = documents["wiki_relations"]
        rules["version"] = "wiki-relations-evidence-v3"
        rules["directions"]["finalizes"] = (
            "A是征求意见稿，B是其正式版本；方向为征求意见稿A指向正式文件B"
        )
        for term in ("调整", "变更"):
            cues = rules.setdefault("kind_cues", {}).setdefault("revises", [])
            if term not in cues:
                cues.append(term)
            targeted = rules.setdefault("targeted_revision_cues", [])
            if term not in targeted:
                targeted.append(term)
        if before == rules:
            self.stdout.write("关系规则已更新，无需重复发布。")
            return
        errors = validate_documents(documents)
        if errors:
            raise CommandError("；".join(errors))
        release = SystemConfigRelease.objects.create(
            version=f"wiki-relations-v3-{timezone.now():%Y%m%d%H%M%S}",
            schema_version=source.schema_version,
            status="published",
            published_at=timezone.now(),
            checksum=checksum(documents),
        )
        SystemConfigDocument.objects.bulk_create(
            [
                SystemConfigDocument(
                    release=release, key=key, content=value, checksum=checksum(value)
                )
                for key, value in documents.items()
            ]
        )
        source.status = "archived"
        source.save(update_fields=["status", "updated_at"])
        SystemConfigAudit.objects.create(
            release=release,
            namespace="wiki_relations",
            action="published",
            before=before,
            after=rules,
            reason="关系二次校验优化：修正征求转正式方向，补充需明确指向旧文件的调整词。其他配置保持不变。",
        )
        clear_config_cache()
        self.stdout.write(f"已发布配置 {release.version}，原配置可回滚。")
