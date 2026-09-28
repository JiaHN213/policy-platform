import json
from collections import Counter

from core.models import AuditRecord
from django.core.management.base import BaseCommand
from django.db import transaction

from policies.enrichment import infer_policy_validity
from policies.models import Policy


class Command(BaseCommand):
    help = "依据原文中的明确状态或已经结束的目标周期回填政策效力；默认仅预览。"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="实际写入高置信度结果")
        parser.add_argument("--verbose", action="store_true", help="逐条输出匹配详情")

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        matched = 0
        updated = 0
        status_counts = Counter()
        queryset = Policy.objects.filter(validity_status="unverified").order_by("id")
        for policy in queryset.iterator():
            inferred = infer_policy_validity(policy)
            if not inferred:
                continue
            matched += 1
            status_counts[inferred["status"]] += 1
            if options["verbose"]:
                self.stdout.write(
                    json.dumps(
                        {
                            "id": str(policy.pk),
                            "publication_date": policy.publication_date.isoformat(),
                            "status": inferred["status"],
                            "title": policy.title[:60],
                            "quote": inferred["quote"][:100],
                        },
                        ensure_ascii=True,
                    )
                )
            if not apply_changes:
                continue
            with transaction.atomic():
                current = Policy.objects.select_for_update().get(pk=policy.pk)
                if current.validity_status != "unverified":
                    continue
                current.validity_status = inferred["status"]
                current.validity_evidence = inferred["quote"]
                current.save(update_fields=["validity_status", "validity_evidence", "updated_at"])

                job = (
                    current.enrichments.filter(policy_version=current.version, status="succeeded")
                    .order_by("-updated_at")
                    .first()
                )
                if job and job.result.get("review"):
                    result = dict(job.result)
                    review = dict(result["review"])
                    review["validity_status"] = inferred["status"]
                    evidence = list(review.get("evidence") or [])
                    evidence = [item for item in evidence if item.get("field") != "validity"]
                    validity_evidence = {"field": "validity", "quote": inferred["quote"]}
                    if len(evidence) < 8:
                        evidence.append(validity_evidence)
                    else:
                        evidence[-1] = validity_evidence
                    review["evidence"] = evidence
                    warnings = list(review.get("warnings") or [])
                    if inferred["warning"] not in warnings:
                        warnings.append(inferred["warning"])
                    review["warnings"] = warnings[:5]
                    result["review"] = review
                    job.result = result
                    job.prompt_version = "policy-enrichment-v4"
                    job.save(update_fields=["result", "prompt_version", "updated_at"])

                AuditRecord.objects.create(
                    actor=None,
                    action="policy.validity.backfilled",
                    object_id=current.pk,
                    details={
                        "version": current.version,
                        "validity_status": inferred["status"],
                        "evidence_quote": inferred["quote"],
                        "method": inferred["warning"],
                    },
                )
                updated += 1

        mode = "已更新" if apply_changes else "预览匹配"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} {updated if apply_changes else matched} 条政策；"
                f"状态分布：{dict(status_counts)}。"
            )
        )
