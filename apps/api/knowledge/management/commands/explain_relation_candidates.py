from core.models import AuditRecord
from django.core.management.base import BaseCommand
from django.db import transaction

from knowledge.models import RelationReviewCandidate
from knowledge.relations import candidate_diagnostics


class Command(BaseCommand):
    help = "补充待人工复审关系的具体原因；不批准关系、不修改原文或模型引用。"

    def handle(self, *args, **options):
        updated = 0
        for pk in RelationReviewCandidate.objects.filter(status="pending").values_list(
            "pk", flat=True
        ):
            with transaction.atomic():
                item = (
                    RelationReviewCandidate.objects.select_for_update(of=("self",))
                    .select_related("scan", "from_policy", "to_policy", "evidence_policy")
                    .get(pk=pk)
                )
                if item.status != "pending":
                    continue
                reason = candidate_diagnostics(item)
                if reason != item.rejection_reason:
                    AuditRecord.objects.create(
                        action="wiki.relation_candidate.explained",
                        object_id=item.pk,
                        details={"before": item.rejection_reason, "after": reason},
                    )
                    item.rejection_reason = reason
                    item.save(update_fields=["rejection_reason", "updated_at"])
                    updated += 1
        self.stdout.write(f"已补充 {updated} 条候选的具体核验原因；未自动确认任何关系。")
