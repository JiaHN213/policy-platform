from django.core.management.base import BaseCommand

from policies.enrichment import assess_attachment_sufficiency, process_one
from policies.models import Policy, PolicyEnrichment


class Command(BaseCommand):
    help = "Re-evaluate attachment-blocked AI results without calling the model again."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        candidates = (
            Policy.objects.filter(status=Policy.Status.CANDIDATE)
            .prefetch_related("snapshots", "discovereditem_set", "enrichments")
            .order_by("created_at", "id")
        )
        eligible = []
        for policy in candidates:
            job = next(
                (
                    item
                    for item in sorted(
                        policy.enrichments.all(), key=lambda value: value.updated_at, reverse=True
                    )
                    if item.policy_version == policy.version and item.status == "succeeded"
                ),
                None,
            )
            if not job:
                continue
            result = job.result or {}
            if (result.get("review") or {}).get("decision") != "include":
                continue
            if "attachments_incomplete" not in (
                (result.get("finalization") or {}).get("blocking_reasons") or []
            ):
                continue
            assessment = assess_attachment_sufficiency(policy, result)
            if assessment["sufficient"] and not assessment["complete"]:
                eligible.append((policy, job, assessment))

        self.stdout.write(
            f"Found {len(eligible)} attachment-blocked policies whose official body is sufficient."
        )
        if not options["apply"]:
            self.stdout.write("Dry run only. Pass --apply to finalize these existing AI results.")
            return

        published = failed = 0
        for policy, job, _assessment in eligible:
            PolicyEnrichment.objects.filter(pk=job.pk).update(
                status="queued",
                attempts=0,
                lease_until=None,
                retry_at=None,
                error_code="",
                result={**job.result, "reuse_validated_ai_result": True},
            )
            process_one(job.pk)
            policy.refresh_from_db(fields=["status"])
            if policy.status == Policy.Status.PUBLISHED:
                published += 1
            else:
                failed += 1
                self.stderr.write(f"Not published: {policy.pk} {policy.title}")
        self.stdout.write(self.style.SUCCESS(f"Published {published}; unresolved {failed}."))
