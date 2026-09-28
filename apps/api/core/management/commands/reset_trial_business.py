"""Explicit pre-launch reset: retain identities/configuration and back up before deletion."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from ingestion.models import DiscoveredItem, Source, SourceCheckRun
from policies.models import (
    DocumentSnapshot,
    Evidence,
    Opportunity,
    OpportunityBatch,
    Policy,
    PolicyEnrichment,
    PolicyRelation,
    PublicationEvent,
)
from subscriptions.models import Notification, Subscription

from core.models import AuditRecord


class Command(BaseCommand):
    help = "备份后清空试用业务数据，保留账号、权限、来源配置和原始文件。必须先停止全部执行器。"

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--prepare", action="store_true")
        parser.add_argument("--manifest")

    def handle(self, *args, **options):
        if options["execute"]:
            return self.execute_manifest(options["manifest"])
        targets = list(
            Policy.objects.filter(
                Q(is_demo=True) | Q(source_url__startswith="https://www.gov.cn/zhengce/")
            ).values("id", "title", "source_url", "is_demo", "version")
        )
        sources = list(
            Source.objects.filter(
                url="https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary"
            ).values_list("id", flat=True)
        )
        manifest = {
            "policies": targets,
            "source_ids": sources,
            "subscription_ids": list(Subscription.objects.values_list("id", flat=True)),
            "discovered_ids": list(
                DiscoveredItem.objects.filter(source_id__in=sources).values_list("id", flat=True)
            ),
            "run_ids": list(
                SourceCheckRun.objects.filter(source_id__in=sources).values_list("id", flat=True)
            ),
        }
        self.stdout.write(
            json.dumps(
                {
                    "policy_targets": len(targets),
                    "subscription_targets": len(manifest["subscription_ids"]),
                    "preserve": "accounts, configuration, raw files, ALL existing audit records",
                },
                ensure_ascii=False,
            )
        )
        if not options["prepare"]:
            return
        if connection.vendor != "postgresql":
            raise CommandError("This reset requires a verified PostgreSQL backup.")
        root = Path(settings.BASE_DIR).resolve()
        backup = root / ".local" / "backups" / timezone.now().strftime("prelaunch-%Y%m%d-%H%M%S")
        backup.mkdir(parents=True, exist_ok=False)
        db = connection.settings_dict
        env = dict(
            os.environ,
            PGHOST=str(db["HOST"]),
            PGPORT=str(db["PORT"]),
            PGUSER=db["USER"],
            PGPASSWORD=db["PASSWORD"],
            PGDATABASE=db["NAME"],
        )
        binary = root / ".local" / "postgres" / "Library" / "bin"
        dump = backup / "database.dump"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            subprocess.run(
                [str(binary / "pg_dump.exe"), "-Fc", "--file", str(dump)],
                env=env,
                check=True,
                capture_output=True,
                timeout=180,
                creationflags=flags,
            )
            subprocess.run(
                [str(binary / "pg_restore.exe"), "--list", str(dump)],
                check=True,
                capture_output=True,
                timeout=30,
                creationflags=flags,
            )
        except subprocess.SubprocessError as exc:
            raise CommandError("Backup verification failed; no data deleted.") from exc
        if dump.stat().st_size < 1000:
            raise CommandError("Backup is unexpectedly small; no data deleted.")
        manifest.update(
            {
                "sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
                "preserved": "accounts, permissions, source configurations, original files, all existing audit records",
            }
        )
        (backup / "manifest.json").write_text(
            json.dumps(manifest, default=str, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.stdout.write(
            f"Backup and exact-ID cleanup manifest ready: {backup / 'manifest.json'}; no rows deleted."
        )

    def execute_manifest(self, path):
        if not path:
            raise CommandError("An explicitly prepared manifest is required.")
        path = Path(path).resolve()
        backup_root = (Path(settings.BASE_DIR) / ".local" / "backups").resolve()
        if not path.is_relative_to(backup_root):
            raise CommandError("Manifest must be within project backup directory.")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (
            hashlib.sha256(path.with_name("database.dump").read_bytes()).hexdigest()
            != manifest["sha256"]
        ):
            raise CommandError("Backup changed; refusing cleanup.")
        ids = [p["id"] for p in manifest["policies"]]
        with transaction.atomic():
            current = {str(p.pk): p for p in Policy.objects.select_for_update().filter(pk__in=ids)}
            for entry in manifest["policies"]:
                p = current.get(entry["id"])
                if (
                    not p
                    or p.version != entry["version"]
                    or p.source_url != entry["source_url"]
                    or (
                        not p.is_demo and not p.source_url.startswith("https://www.gov.cn/zhengce/")
                    )
                ):
                    raise CommandError(
                        "Target policy changed or is outside approved trial-source scope."
                    )
            Source.objects.filter(pk__in=manifest["source_ids"]).update(
                enabled=False, next_check_at=None
            )
            Notification.objects.filter(event__policy_id__in=ids).delete()
            PublicationEvent.objects.filter(policy_id__in=ids).delete()
            OpportunityBatch.objects.filter(opportunity__policy_id__in=ids).delete()
            Opportunity.objects.filter(policy_id__in=ids).delete()
            PolicyRelation.objects.filter(from_policy_id__in=ids, to_policy_id__in=ids).delete()
            PolicyEnrichment.objects.filter(policy_id__in=ids).delete()
            DiscoveredItem.objects.filter(pk__in=manifest["discovered_ids"]).delete()
            SourceCheckRun.objects.filter(pk__in=manifest["run_ids"]).delete()
            DocumentSnapshot.objects.filter(policy_id__in=ids).delete()
            Evidence.objects.filter(policy_id__in=ids).delete()
            Policy.objects.filter(pk__in=ids).delete()
            Subscription.objects.filter(pk__in=manifest["subscription_ids"]).delete()
            AuditRecord.objects.create(
                action="business.prelaunch_reset",
                object_id=__import__("uuid").uuid4(),
                details={"manifest": str(path), "policy_ids": ids},
            )
        self.stdout.write(
            f"Exact manifest cleanup completed: {len(ids)} trial policies; raw files and existing audit records preserved."
        )
