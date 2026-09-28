from pathlib import PurePosixPath
from urllib.parse import urlparse

from core.storage import read_original
from django.core.management.base import BaseCommand
from django.db import transaction
from ingestion.diagnostics import attachment_failure
from ingestion.documents import extract_attachment_text, normalize_extracted_text
from ingestion.gov_library import SourceUnavailable

from policies.models import Policy, PolicyEnrichment
from policies.services import fingerprint


def _same_attachment(left, right):
    left_url, right_url = urlparse(left), urlparse(right)
    return (left_url.hostname, left_url.path) == (right_url.hostname, right_url.path)


class Command(BaseCommand):
    help = "Reparse stored attachments for AI-included candidate policies and queue changed text."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        candidates = (
            Policy.objects.filter(status=Policy.Status.CANDIDATE)
            .prefetch_related("snapshots", "discovereditem_set", "enrichments")
            .order_by("created_at", "id")
        )
        targets = []
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
            if not job or (job.result.get("review") or {}).get("decision") != "include":
                continue
            pending = [item for item in policy.snapshots.all() if item.parse_status != "parsed"]
            if pending:
                targets.append((policy, pending))
            if len(targets) >= max(1, options["limit"]):
                break
        self.stdout.write(f"Found {len(targets)} policies with stored unresolved attachments.")
        if not options["apply"]:
            self.stdout.write("Dry run only. Pass --apply to parse supported stored originals.")
            return

        changed_policies = parsed_files = unsupported_files = 0
        for candidate, _pending in targets:
            with transaction.atomic():
                policy = Policy.objects.select_for_update().get(pk=candidate.pk)
                additions = []
                parsed_urls = []
                failed_urls = {}
                for snapshot in policy.snapshots.select_for_update().exclude(
                    parse_status="parsed"
                ):
                    suffix = PurePosixPath(urlparse(snapshot.url).path).suffix.lstrip(".")
                    try:
                        text = extract_attachment_text(read_original(snapshot.object_key), suffix)
                        if len(text.strip()) < 10:
                            raise SourceUnavailable("ATTACHMENT_TEXT_EMPTY")
                    except Exception as exc:
                        unsupported_files += 1
                        failed_urls[snapshot.url] = attachment_failure(exc, "parse")
                        continue
                    label = PurePosixPath(urlparse(snapshot.url).path).name or "官方附件"
                    marker = f"附件：{label}"
                    if marker not in policy.body:
                        additions.append(f"{marker}\n{text}")
                    snapshot.parse_status = "parsed"
                    snapshot.save(update_fields=["parse_status", "updated_at"])
                    parsed_urls.append(snapshot.url)
                    parsed_files += 1

                for item in policy.discovereditem_set.select_for_update():
                    metadata = dict(item.metadata or {})
                    issues = metadata.get("attachment_issues") or []
                    remaining = [
                        issue
                        for issue in issues
                        if not any(
                            _same_attachment(issue.get("url", ""), parsed_url)
                            for parsed_url in parsed_urls
                        )
                    ]
                    for url, reason in failed_urls.items():
                        matching = next((issue for issue in remaining if _same_attachment(issue.get("url", ""), url)), None)
                        if matching is not None:
                            matching["reason"] = reason
                        else:
                            remaining.append({"url": url, "reason": reason})
                    metadata["attachment_issues"] = remaining
                    item.metadata = metadata
                    if not remaining and item.error_code == "ATTACHMENTS_REQUIRE_REVIEW":
                        item.error_code = ""
                    elif remaining:
                        item.error_code = "ATTACHMENTS_REQUIRE_REVIEW"
                    item.save(update_fields=["metadata", "error_code", "updated_at"])

                if not parsed_urls:
                    continue

                if additions:
                    policy.body = normalize_extracted_text(
                        policy.body + "\n\n" + "\n\n".join(additions)
                    )
                    policy.content_hash = fingerprint(policy.body)
                    policy.version += 1
                    policy.extraction_version = 0
                    policy.save(
                        update_fields=[
                            "body",
                            "content_hash",
                            "version",
                            "extraction_version",
                            "updated_at",
                        ]
                    )
                    PolicyEnrichment.objects.get_or_create(
                        policy=policy,
                        policy_version=policy.version,
                        defaults={"prompt_version": "policy-enrichment-v6"},
                    )
                    changed_policies += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Parsed {parsed_files} attachments; queued {changed_policies} changed policies; "
                f"still unsupported {unsupported_files}."
            )
        )
