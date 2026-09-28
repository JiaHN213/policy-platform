import time
from datetime import date
from urllib.parse import urlparse

from core.models import AuditRecord
from core.storage import store_original
from django.db import transaction
from django.utils import timezone
from policies.business_scope import assess_scope
from policies.classification import suggest_document_type
from policies.extraction import extract_metadata
from policies.models import DocumentSnapshot, Evidence, Policy
from policies.provenance import attach_policy_source, find_canonical_policy
from policies.services import fingerprint

from .diagnostics import attachment_failure
from .documents import extract_attachment_text, parse_pdf, parse_policy_html
from .gov_library import SourceUnavailable, fetch_resource
from .models import DiscoveredItem


def import_record(record, source, claim=None):
    publication_date = record.get("publication_date")
    if isinstance(publication_date, str):
        try:
            publication_date = date.fromisoformat(publication_date)
        except ValueError as exc:
            raise SourceUnavailable("PUBLICATION_DATE_UNVERIFIED") from exc
        record = {**record, "publication_date": publication_date}
    if not isinstance(publication_date, date):
        raise SourceUnavailable("PUBLICATION_DATE_UNVERIFIED")
    data, content_type, resolved_url = fetch_resource(record["url"])
    parsed = parse_policy_html(data, resolved_url)
    original = store_original(data, content_type)
    snapshots = [{**original, "url": resolved_url, "content_type": content_type,
                  "parse_status": "parsed"}]
    evidence = [
        {
            "text": parsed["text"],
            "location": {
                "kind": "html",
                "source_url": resolved_url,
                "selector": "#UCAP-CONTENT",
                "sha256": original["sha256"],
            },
        }
    ]
    body_parts = [parsed["text"]]
    attachment_issues = []
    for url, title in parsed["attachments"].items():
        time.sleep(1)
        stage = "download"
        try:
            data, content_type, final_url = fetch_resource(
                url, ("application/pdf", "application/octet-stream", "application/msword",
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                      "application/vnd.ms-excel", "application/ofd", "application/zip",
                      "application/x-zip-compressed"), 30_000_000
            )
            original = store_original(data, content_type)
            snapshot = {**original, "url": final_url, "content_type": content_type,
                        "parse_status": "pending"}
            snapshots.append(snapshot)
            stage = "parse"
            suffix = urlparse(final_url).path.rsplit(".", 1)[-1]
            pages = parse_pdf(data) if data.startswith(b"%PDF-") else [
                {"text": extract_attachment_text(data, suffix)}
            ]
            if not pages or not any(len(page["text"].strip()) >= 10 for page in pages):
                raise SourceUnavailable("ATTACHMENT_TEXT_EMPTY")
            snapshot["parse_status"] = "parsed"
        except Exception as exc:
            attachment_issues.append({"url": url, "title": title,
                                      "reason": attachment_failure(exc, stage)})
            continue
        for page in pages:
            label = f" · 第 {page['page']} 页" if "page" in page else ""
            body_parts.append(f"附件：{title}{label}\n{page['text']}")
            evidence.append(
                {
                    "text": page["text"],
                    "location": {
                        "kind": "pdf" if "page" in page else "attachment",
                        "source_url": final_url,
                        **({"page": page["page"]} if "page" in page else {}),
                        "sha256": original["sha256"],
                    },
                }
            )
    body = "\n\n".join(body_parts)
    scope = assess_scope(record["title"], body)
    if scope["decision"] != "included":
        with transaction.atomic():
            item, _ = DiscoveredItem.objects.select_for_update().get_or_create(
                source=source,
                url=record["url"],
                defaults={
                    "title": record["title"],
                    "metadata": {
                        **record,
                        "publication_date": record["publication_date"].isoformat(),
                    },
                },
            )
            if claim and (
                item.pk != claim[0]
                or item.lease_until != claim[1]
                or item.status != "processing"
                or item.lease_until <= timezone.now()
            ):
                raise SourceUnavailable("IMPORT_LEASE_EXPIRED")
            item.metadata = {**item.metadata, "scope_assessment": scope,
                             "attachment_issues": attachment_issues}
            item.status = "indexed" if scope["decision"] == "needs_review" else "excluded"
            item.lease_until = item.retry_at = None
            item.error_code = item.error_detail = ""
            item.save()
        return None, False, len(snapshots), len(evidence), len(body)
    topics = []
    for topic, words in {
        "水务": ("水务", "水利", "供水", "污水", "水资源"),
        "环保": ("环保", "生态环境", "污染防治", "碳排放"),
        "人工智能＋": ("人工智能", "大模型", "机器学习"),
    }.items():
        if any(word in record["title"] + body for word in words):
            topics.append(topic)
    if not record["issuer"] or len(record["issuer"]) > 200:
        raise SourceUnavailable("Issuer requires manual verification")
    with transaction.atomic():
        if claim:
            item_id, lease = claim
            item = DiscoveredItem.objects.select_for_update().get(pk=item_id)
            if item.status != "processing" or item.lease_until != lease or lease <= timezone.now():
                raise SourceUnavailable("IMPORT_LEASE_EXPIRED")
        body_hash = fingerprint(body)
        policy = Policy.objects.filter(source_key=fingerprint(record["url"])).first()
        match_method, confidence, match_evidence = "source_url", 1, {"url": record["url"]}
        if policy is None:
            policy, match_method, confidence, match_evidence = find_canonical_policy(
                record, body_hash
            )
        created = policy is None
        if created:
            policy = Policy.objects.create(**{
                "source_key": fingerprint(record["url"]),
                **{k: v for k, v in record.items() if k != "url"},
                "source_url": record["url"],
                "body": body,
                "content_hash": body_hash,
                "topics": topics,
                "industry": "water_environment",
                "business_domains": scope["business_domains"],
                "direction_tags": scope["direction_tags"],
                "scope_evidence": scope,
                "document_type": suggest_document_type(record["title"]),
                **extract_metadata(body),
                "extraction_version": 1,
            })
        if match_method == "source_url" and policy.content_hash != body_hash:
            raise SourceUnavailable("Existing policy changed; explicit version review required")
        attach_policy_source(
            policy,
            record,
            body_hash,
            resolved_url=resolved_url,
            created_policy=created,
            match_method=match_method,
            confidence=confidence,
            evidence=match_evidence,
        )
        for snapshot in snapshots:
            DocumentSnapshot.objects.get_or_create(
                policy=policy,
                sha256=snapshot["sha256"],
                defaults={k: v for k, v in snapshot.items() if k != "sha256"},
            )
        for item in evidence:
            Evidence.objects.get_or_create(
                policy=policy,
                policy_version=policy.version,
                quote_hash=fingerprint(item["text"]),
                defaults=item,
            )
        if created:
            AuditRecord.objects.create(
                action="policy.import",
                object_id=policy.id,
                details={"source": str(source.id), "snapshots": len(snapshots)},
            )
        DiscoveredItem.objects.update_or_create(
            source=source,
            url=record["url"],
            defaults={
                "title": record["title"],
                "status": "imported",
                "policy": policy,
                "metadata": {
                    **(
                        DiscoveredItem.objects.filter(source=source, url=record["url"])
                        .values_list("metadata", flat=True)
                        .first()
                        or {}
                    ),
                    "scope_assessment": scope,
                    "attachment_issues": attachment_issues,
                },
                "lease_until": None,
                "retry_at": None,
                "error_code": "ATTACHMENTS_REQUIRE_REVIEW" if attachment_issues else "",
                "error_detail": "部分附件未完整解析，请查看各附件的具体原因。" if attachment_issues else "",
            },
        )
    return policy, created, len(snapshots), len(evidence), len(body)
