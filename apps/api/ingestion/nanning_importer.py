import hashlib
import json
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup
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
from .documents import extract_attachment_text, readable_html_text
from .gov_library import SourceUnavailable
from .models import DiscoveredItem
from .nanning import parse_document, request

NPC_HOST = "flk.npc.gov.cn"


def fetch_npc_document(record):
    document_id = (parse_qs(urlparse(record["url"]).query).get("id") or [""])[0]
    if not document_id:
        raise SourceUnavailable("NPC_DOCUMENT_ID_MISSING")
    detail_url = f"https://{NPC_HOST}/law-search/search/flfgDetails?{urlencode({'bbbs': document_id})}"
    detail_bytes, detail_type, _ = request(detail_url)
    if detail_type != "application/json":
        raise SourceUnavailable("NPC_DETAIL_RESPONSE_INVALID")
    try:
        detail_response = json.loads(detail_bytes)
        detail = detail_response["data"]
        if int(detail_response["code"]) != 200 or not detail.get("title"):
            raise ValueError()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SourceUnavailable("NPC_DETAIL_RESPONSE_INVALID") from exc

    raw_content = detail.get("content")
    # The NPC API returns either HTML text or a nested table-of-contents object.
    # A TOC is not the law body, so download the official DOCX in that case.
    direct_content = ""
    if isinstance(raw_content, (str, bytes)):
        direct_content = readable_html_text(BeautifulSoup(raw_content, "html.parser"))
    if len(direct_content) >= 30:
        return direct_content, [], detail

    download_url = f"https://{NPC_HOST}/law-search/download/pc?{urlencode({'format': 'docx', 'bbbs': document_id, 'fileId': ''})}"
    download_bytes, download_type, _ = request(download_url)
    if download_type != "application/json":
        raise SourceUnavailable("NPC_DOWNLOAD_RESPONSE_INVALID")
    try:
        download_response = json.loads(download_bytes)
        signed_url = download_response["data"]["url"]
        if int(download_response["code"]) != 200:
            raise ValueError()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SourceUnavailable("NPC_DOWNLOAD_RESPONSE_INVALID") from exc
    document, content_type, resolved = request(signed_url, max_bytes=30_000_000)
    body = attachment_text(document, "docx")
    if len(body.strip()) < 30:
        raise SourceUnavailable("NPC_DOCUMENT_TEXT_EMPTY")
    snapshot = {
        **store_original(document, content_type),
        "url": resolved,
        "content_type": content_type,
        "parse_status": "parsed",
    }
    return body, [snapshot], detail


def attachment_text(data, suffix):
    return extract_attachment_text(data, suffix)


def import_record(record, source, claim=None):
    data, mime, final = request(record["url"])
    if mime not in {"text/html", "application/xhtml+xml"}:
        raise SourceUnavailable("EXPECTED_POLICY_HTML")
    snapshots = [
        {**store_original(data, mime), "url": final, "content_type": mime, "parse_status": "parsed"}
    ]
    if urlparse(record["url"]).hostname == NPC_HOST:
        body, document_snapshots, detail = fetch_npc_document(record)
        snapshots.extend(document_snapshots)
        attachments = {}
        record = {
            **record,
            "title": detail.get("title") or record["title"],
            "issuer": detail.get("zdjgName") or record.get("issuer", ""),
            "publication_date": detail.get("gbrq") or record["publication_date"],
        }
    else:
        parsed = parse_document(data, final)
        body = parsed["text"]
        attachments = dict(parsed["attachments"])
    attachments.update(record.get("attachments") or {})
    if len(attachments) > 30:
        raise SourceUnavailable("ATTACHMENT_LIMIT_REQUIRES_REVIEW")
    normalized_attachments = {}
    for attachment_url, attachment_title in attachments.items():
        if attachment_url.startswith("http://"):
            attachment_url = "https://" + attachment_url[7:]
        normalized_attachments.setdefault(attachment_url, attachment_title)
    attachments = normalized_attachments
    failed = []
    for url, title in attachments.items():
        try:
            content, ctype, resolved = request(url, max_bytes=30_000_000)
            snapshot = {
                **store_original(content, ctype),
                "url": resolved,
                "content_type": ctype,
                "parse_status": "pending",
            }
            snapshots.append(snapshot)
            try:
                text = attachment_text(content, urlparse(resolved).path.lower().rsplit(".", 1)[-1])
                if len(text.strip()) < 10:
                    raise SourceUnavailable("ATTACHMENT_TEXT_EMPTY")
                body += f"\n附件：{title}\n{text}"
                snapshot["parse_status"] = "parsed"
            except Exception as exc:
                failed.append(
                    {
                        "url": url,
                        "title": title,
                        "content_type": ctype,
                        "reason": attachment_failure(exc, "parse"),
                    }
                )
        except Exception as exc:
            failed.append({"url": url, "title": title,
                           "reason": attachment_failure(exc, "download")})
    scope = assess_scope(record["title"], body)
    with transaction.atomic():
        item = DiscoveredItem.objects.select_for_update().get(source=source, url=record["url"])
        if claim and (
            item.pk != claim[0]
            or item.lease_until != claim[1]
            or item.status != "processing"
            or item.lease_until <= timezone.now()
        ):
            raise SourceUnavailable("IMPORT_LEASE_EXPIRED")
        item.metadata = {
            **item.metadata,
            "scope_assessment": scope,
            "attachment_issues": failed,
            "originals": snapshots,
        }
        # Apply the business-scope decision before metadata and attachment gates.
        # Otherwise an unrelated document with a blank issuer or a broken attachment
        # incorrectly occupies the manual-review queue.
        if scope["decision"] == "excluded":
            item.status = "excluded"
            item.lease_until = None
            item.retry_at = None
            item.error_code = ""
            item.error_detail = ""
            item.save()
            return None, False, len(snapshots), 0, len(body)
        if scope["decision"] == "needs_review":
            # Keep weak, body-only matches as searchable source material without
            # presenting thousands of low-confidence hits as manual tasks.
            item.status = "indexed"
            item.lease_until = None
            item.retry_at = None
            item.error_code = ""
            item.error_detail = ""
            item.save()
            return None, False, len(snapshots), 0, len(body)
        if record.get("title_requires_review") or not record.get("issuer"):
            item.status = "needs_review"
            item.lease_until = None
            item.retry_at = None
            item.error_code = "TITLE_OR_ISSUER_REQUIRES_REVIEW"
            item.save()
            return None, False, len(snapshots), 0, len(body)
        province, city = record.get("province", ""), record.get("city", "")
        national = record.get("db_name", "") in {
            "machining_zckv2_flk_two",
            "machining_zckv2_gw_two",
        }
        level = (
            "national"
            if national
            else ("city" if city else "provincial" if province else "unverified")
        )
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
                "title": record["title"],
                "issuer": record["issuer"][:200],
                "document_number": record.get("document_number", "")[:200],
                "publication_date": record["publication_date"],
                "source_url": record["url"],
                "body": body,
                "content_hash": body_hash,
                "region": city or province or "全国",
                "province": "" if national else province,
                "city": "" if national else city,
                "geographic_level": level,
                "topics": ["水务", "环保"],
                "industry": "water_environment",
                "business_domains": scope["business_domains"],
                "direction_tags": scope["direction_tags"],
                "scope_evidence": scope,
                "document_type": suggest_document_type(record["title"]),
                **extract_metadata(body),
                "extraction_version": 1,
            })
        if match_method == "source_url" and policy.content_hash != body_hash:
            raise SourceUnavailable("CONTENT_CHANGED_REQUIRES_VERSION_REVIEW")
        attach_policy_source(
            policy,
            record,
            body_hash,
            resolved_url=final,
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
        Evidence.objects.get_or_create(
            policy=policy,
            policy_version=policy.version,
            quote_hash=hashlib.sha256(body.encode()).hexdigest(),
            defaults={
                "text": body,
                "location": {"kind": "body_and_attachments", "source_url": final},
            },
        )
        item.policy = policy
        item.status = "imported"
        item.parsed_at = timezone.now()
        item.lease_until = None
        item.retry_at = None
        item.error_code = "ATTACHMENTS_REQUIRE_REVIEW" if failed else ""
        item.save()
    return policy, created, len(snapshots), 1, len(body)
