"""Public Nanning policy-library adapter with date partitions and durable page receipts."""

import hashlib
import ipaddress
import json
import random
import socket
import time
from datetime import date, datetime, timedelta
from datetime import timezone as datetime_timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from core.business_config import get_config
from core.storage import store_original
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from policies.business_scope import SEARCH_TERMS, assess_scope

from .documents import decode_html, readable_html_text
from .gov_library import SourceUnavailable
from .models import CrawlPage, CrawlThrottle, DiscoveredItem, SourceCheckRun

URL = "https://www.nanning.gov.cn/sousuo/zck/"
TAB = "ee993589be885591fb2e1889b3497620"
PRIMARY_TERMS = get_config("business_scope", database=False).get(
    "primary_collection_terms", []
)


def compact_terms(terms):
    """Drop a longer phrase when an earlier broader term already contains it."""
    compacted = []
    for term in dict.fromkeys(terms):
        if not any(existing in term for existing in compacted):
            compacted.append(term)
    return compacted


TERMS = compact_terms(PRIMARY_TERMS + SEARCH_TERMS)


def validate_target(url):
    """Validate an outbound URL and report whether DNS is using a VPN Fake-IP."""
    p = urlparse(url)
    host = p.hostname or ""
    if (
        p.scheme not in {"https", "http"}
        or p.username
        or p.password
        or p.port not in (None, 443, 80)
        or not (
            host.endswith(".gov.cn")
            or host in {"api.so-gov.cn", "flkoss.obs-bj2.cucloud.cn"}
        )
    ):
        raise SourceUnavailable("NANNING_URL_NOT_ALLOWED")
    fake_ip = False
    port = p.port or (443 if p.scheme == "https" else 80)
    for address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(address[4][0])
        fake = ip.version == 4 and ip in ipaddress.ip_network("198.18.0.0/15")
        if fake:
            fake_ip = True
        elif not ip.is_global:
            raise SourceUnavailable("NON_PUBLIC_ADDRESS")
    return fake_ip


def _proxy_available(proxy_url):
    parsed = urlparse(proxy_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        connection = socket.create_connection((parsed.hostname, port), timeout=0.5)
        connection.close()
        return True
    except OSError:
        return False


def _select_proxy(url):
    """Use the local VPN proxy only while DNS is returning a Fake-IP."""
    if not validate_target(url):
        return None
    proxy_url = settings.POLICY_CRAWL_PROXY_URL.strip()
    if not proxy_url:
        raise SourceUnavailable("VPN_FAKE_IP_PROXY_REQUIRED")
    if not _proxy_available(proxy_url):
        raise SourceUnavailable("VPN_PROXY_UNAVAILABLE")
    return proxy_url


THROTTLE_SCOPE = "nanning-policy-source"


def _user_agent():
    contact = settings.POLICY_CRAWLER_CONTACT.strip()
    suffix = f"; contact={contact}" if contact else ""
    return f"PolicyObserver/1.0 (government-policy-monitor{suffix})"


def _retry_after_seconds(value):
    if not value:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime_timezone.utc)
            return max(0, int((parsed - datetime.now(datetime_timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return 0


def activate_cooldown(status_code, retry_after=""):
    """Stop every worker using this source after a rejection response."""
    default_minutes = (
        settings.POLICY_CRAWL_FORBIDDEN_COOLDOWN_MINUTES
        if status_code == 403
        else settings.POLICY_CRAWL_RATE_LIMIT_COOLDOWN_MINUTES
    )
    seconds = max(default_minutes * 60, _retry_after_seconds(retry_after))
    blocked_until = timezone.now() + timedelta(seconds=seconds)
    with transaction.atomic():
        throttle, _ = CrawlThrottle.objects.select_for_update().get_or_create(scope=THROTTLE_SCOPE)
        throttle.blocked_until = max(filter(None, [throttle.blocked_until, blocked_until]))
        throttle.last_status_code = status_code
        throttle.rejection_count += 1
        throttle.save(
            update_fields=[
                "blocked_until",
                "last_status_code",
                "rejection_count",
                "updated_at",
            ]
        )
    return blocked_until


def cooldown_until():
    return (
        CrawlThrottle.objects.filter(scope=THROTTLE_SCOPE)
        .values_list("blocked_until", flat=True)
        .first()
    )


def _reserve_request_slot():
    """Reserve one source-wide slot so separate workers cannot create a burst."""
    with transaction.atomic():
        throttle, _ = CrawlThrottle.objects.select_for_update().get_or_create(scope=THROTTLE_SCOPE)
        now = timezone.now()
        if throttle.blocked_until and throttle.blocked_until > now:
            raise SourceUnavailable("NANNING_COOLDOWN_ACTIVE")
        scheduled_at = max(filter(None, [now, throttle.next_request_at]))
        interval = settings.POLICY_CRAWL_MIN_INTERVAL_SECONDS + random.uniform(
            0, settings.POLICY_CRAWL_JITTER_SECONDS
        )
        throttle.next_request_at = scheduled_at + timedelta(seconds=interval)
        throttle.save(update_fields=["next_request_at", "updated_at"])
    wait_seconds = (scheduled_at - timezone.now()).total_seconds()
    if wait_seconds > 0:
        time.sleep(wait_seconds)


def _record_success(status_code):
    now = timezone.now()
    CrawlThrottle.objects.filter(scope=THROTTLE_SCOPE).filter(
        Q(blocked_until__isnull=True) | Q(blocked_until__lte=now)
    ).update(
        blocked_until=None,
        last_status_code=status_code,
        rejection_count=0,
        updated_at=now,
    )
    # A successful source request proves an earlier shared cooldown has ended.
    # Release items parked until the old cooldown deadline instead of leaving
    # them displayed as failures for hours.
    DiscoveredItem.objects.filter(
        status="failed", error_code="NANNING_COOLDOWN_ACTIVE", attempts__lt=3
    ).update(
        status="discovered",
        error_code="",
        error_detail="",
        retry_at=None,
        lease_until=None,
        updated_at=now,
    )


def request(url, data=None, max_bytes=20_000_000):
    for attempt in range(3):
        try:
            return _request_once(url, data, max_bytes)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in {
                429,
                500,
                502,
                503,
                504,
            }:
                raise
            message = str(exc).upper()
            legacy_tls_failure = any(
                marker in message
                for marker in (
                    "CERTIFICATE_VERIFY_FAILED",
                    "TLSV1_UNRECOGNIZED_NAME",
                    "SSLV3_ALERT_HANDSHAKE_FAILURE",
                    "UNEXPECTED_EOF_WHILE_READING",
                )
            )
            parsed = urlparse(url)
            if (
                legacy_tls_failure
                and parsed.scheme == "https"
                and parsed.hostname
                and parsed.hostname.endswith(".gov.cn")
                and settings.POLICY_CRAWL_ALLOW_LEGACY_HTTP
            ):
                url = parsed._replace(scheme="http", netloc=parsed.hostname).geturl()
                continue
            if attempt == 2:
                raise
            time.sleep(random.uniform(1, 2 ** (attempt + 1)))


def _request_once(url, data, max_bytes):
    for _ in range(4):
        proxy = _select_proxy(url)
        _reserve_request_slot()
        # A short-lived client prevents stale VPN connections and exhausted pools
        # from surviving a FlClash on/off switch during a long historical crawl.
        with httpx.Client(
            trust_env=False,
            timeout=httpx.Timeout(30, pool=30),
            follow_redirects=False,
            proxy=proxy,
        ) as client:
            with client.stream(
                "POST" if data is not None else "GET",
                url,
                data=data,
                headers={"User-Agent": _user_agent(), "Referer": URL},
            ) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code in {403, 429}:
                    activate_cooldown(response.status_code, response.headers.get("retry-after", ""))
                    code = (
                        "NANNING_ACCESS_RESTRICTED"
                        if response.status_code == 403
                        else "NANNING_RATE_LIMITED"
                    )
                    raise SourceUnavailable(code)
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > max_bytes:
                        raise SourceUnavailable("BODY_TOO_LARGE")
                _record_success(response.status_code)
                return (
                    bytes(content),
                    response.headers.get("content-type", "").split(";")[0],
                    url,
                )
    raise SourceUnavailable("TOO_MANY_REDIRECTS")


def list_page(query="", page=1, start="1000-01-01", end="2999-12-31"):
    payload, _, _ = request(
        "https://api.so-gov.cn/query/s",
        data={
            "siteCode": "4501000060_zck",
            "tab": TAB,
            "qt": query,
            "page": page,
            "pageSize": 20,
            "keyPlace": 0,
            "sort": "dateDesc",
            "timeOption": 2,
            "startDateStr": start,
            "endDateStr": end,
        },
    )
    try:
        data = json.loads(payload)
        if data.get("code") == -101:
            activate_cooldown(403)
            raise SourceUnavailable("NANNING_ACCESS_RESTRICTED")
        if not data["ok"] or data["code"] != 200 or data["data"]["config"]["id"] != TAB:
            raise ValueError()
        search = data["data"]["result"]["search"]
        total = int(search["total"])
        docs = search["docs"]
        if not isinstance(docs, list) or len(docs) != min(20, max(0, total - (page - 1) * 20)):
            raise ValueError()
        for row in docs:
            day = row.get("docDate", "")[:10]
            if not day or not start <= day <= end:
                raise ValueError()
        return {"total": total, "docs": docs, "raw": payload}
    except (ValueError, TypeError, KeyError) as exc:
        raise SourceUnavailable("NANNING_PAGE_STRUCTURE_OR_DATE_MISMATCH") from exc


def record(row):
    values = row.get("myValues") or {}
    title = BeautifulSoup(row.get("titleO") or row.get("title") or "", "html.parser").get_text(
        " ", strip=True
    )
    url = urldefrag(row["url"])[0]
    title_issue = not title or len(title) > 500
    original_title = title
    title = title[:500] if title else f"【来源标题待核实】{row['id']}"
    return {
        "url": url,
        "title": title,
        "original_title": original_title,
        "title_requires_review": title_issue,
        "publication_date": row["docDate"][:10],
        "issuer": values.get("DOCPUBNAME") or "",
        "document_number": values.get("DOCNOVAL") or "",
        "index_summary": row.get("summary") or "",
        "province": values.get("SHENG") or "",
        "city": values.get("SHI") or "",
        "source_document_id": str(row["id"]),
        "attachments": values.get("ATTACHMENTS") or {},
        "db_name": row.get("dbName", ""),
    }


def new_progress(start="1000-01-01", end="2999-12-31"):
    mode = "full" if start == "1000-01-01" and end == "2999-12-31" else "incremental"
    return {
        "version": 4,
        "mode": mode,
        "window_start": start,
        "window_end": end,
        # Historical initialization searches only business terms. A blank historical
        # query would download the site's entire unrelated catalog. The small daily
        # window includes a blank query so newly coined water-policy language is seen.
        "queries": ([""] if mode == "incremental" else []) + TERMS,
        "query_index": 0,
        "parts": [{"start": start, "end": end, "page": 1}],
        "rows_scanned": 0,
        "pages_scanned": 0,
        "catalog_total": None,
        "catalog_rows": 0,
        "listing_complete": False,
    }


def _upgrade_progress(state):
    """Move an unfinished legacy full-catalog run onto targeted historical searches."""
    version = state.get("version", 1)
    if version < 3 and state.get("mode", "full") == "full":
        old_queries = state.get("queries") or [""] + TERMS
        old_index = state.get("query_index", 0)
        current_query = old_queries[old_index] if old_index < len(old_queries) else None
        state["queries"] = TERMS.copy()
        if current_query == "":
            state["query_index"] = 0
            state["parts"] = [
                {"start": state["window_start"], "end": state["window_end"], "page": 1}
            ]
        elif current_query in TERMS:
            state["query_index"] = TERMS.index(current_query)
        else:
            state["query_index"] = len(TERMS)
        state["catalog_total"] = None
        state["catalog_rows"] = 0
        version = 3
    if version < 4:
        old_queries = state.get("queries") or []
        old_index = state.get("query_index", 0)
        current_query = old_queries[old_index] if old_index < len(old_queries) else None
        target = ([""] if state.get("mode") == "incremental" else []) + TERMS
        if current_query in target:
            next_index = target.index(current_query)
        else:
            remaining = [
                query
                for query in target
                if query in old_queries and old_queries.index(query) > old_index
            ]
            next_index = target.index(remaining[0]) if remaining else len(target)
            if next_index < len(target):
                state["parts"] = [
                    {"start": state["window_start"], "end": state["window_end"], "page": 1}
                ]
        state["queries"] = target
        state["query_index"] = next_index
        state["version"] = 4
    return state


def advance(run_id, budget=1):
    """One bounded worker slice; each page and its cursor commit atomically."""
    for _ in range(budget):
        run = SourceCheckRun.objects.select_related("source").get(pk=run_id)
        state = run.progress or new_progress()
        state.setdefault("mode", "full")
        state.setdefault("window_start", "1000-01-01")
        state.setdefault("window_end", "2999-12-31")
        state = _upgrade_progress(state)
        if state["query_index"] >= len(state["queries"]):
            state["listing_complete"] = True
            if (
                state["catalog_total"] is not None
                and state["catalog_rows"] != state["catalog_total"]
            ):
                raise SourceUnavailable("CATALOG_COUNT_MISMATCH")
            SourceCheckRun.objects.filter(pk=run_id).update(
                progress=state,
                status="succeeded",
                finished_at=timezone.now(),
                error_message=(
                    "增量列表及水务全文召回已完成；正文/附件由候选导入任务串行处理。"
                    if state["mode"] == "incremental"
                    else "全量列表及水务全文召回已完成；正文/附件由候选导入任务串行处理。"
                ),
            )
            run.source.last_success_at = timezone.now()
            run.source.save(update_fields=["last_success_at"])
            return
        query = state["queries"][state["query_index"]]
        part = state["parts"][0]
        result = list_page(query, part["page"], part["start"], part["end"])
        total = result["total"]
        if not query and state["catalog_total"] is None:
            state["catalog_total"] = total
        if total > 1000:
            start, end = date.fromisoformat(part["start"]), date.fromisoformat(part["end"])
            if start == end:
                raise SourceUnavailable("DATE_PARTITION_TOO_DENSE")
            mid = start + (end - start) // 2
            state["parts"] = [
                {"start": (mid + timedelta(days=1)).isoformat(), "end": part["end"], "page": 1},
                {"start": part["start"], "end": mid.isoformat(), "page": 1},
            ] + state["parts"][1:]
            SourceCheckRun.objects.filter(pk=run_id).update(
                progress=state, lease_until=timezone.now() + timedelta(minutes=10)
            )
            continue
        if "total" in part and part["total"] != total:
            raise SourceUnavailable("PARTITION_TOTAL_CHANGED")
        part["total"] = total
        identity = hashlib.sha256(
            json.dumps([r.get("id") for r in result["docs"]]).encode()
        ).hexdigest()
        key = f"{query}|{part['start']}|{part['end']}"
        if (
            result["docs"]
            and CrawlPage.objects.filter(run_id=run_id, query=key, response_hash=identity)
            .exclude(page=part["page"])
            .exists()
        ):
            raise SourceUnavailable("PAGINATION_REPEATED")
        original = store_original(result["raw"], "application/json")
        with transaction.atomic():
            locked = SourceCheckRun.objects.select_for_update().get(pk=run_id)
            for row in result["docs"]:
                item = record(row)
                match = assess_scope(item["title"], item["index_summary"])
                needs_body = bool(query) or bool(match["business_domains"])
                obj, created = DiscoveredItem.objects.get_or_create(
                    source=run.source,
                    url=item["url"],
                    defaults={
                        "title": item["title"],
                        "metadata": item,
                        "status": "discovered" if needs_body else "indexed",
                    },
                )
                if needs_body and obj.status == "indexed":
                    obj.status = "discovered"
                    obj.save(update_fields=["status"])
                locked.discovered += int(created)
            CrawlPage.objects.update_or_create(
                run_id=run_id,
                query=key,
                page=part["page"],
                defaults={
                    "total": total,
                    "item_count": len(result["docs"]),
                    "response_hash": identity,
                    "object_key": original["object_key"],
                },
            )
            state["rows_scanned"] += len(result["docs"])
            state["pages_scanned"] += 1
            if not query:
                state["catalog_rows"] += len(result["docs"])
            if part["page"] * 20 >= total:
                state["parts"].pop(0)
                if not state["parts"]:
                    state["query_index"] += 1
                    state["parts"] = [
                        {
                            "start": state["window_start"],
                            "end": state["window_end"],
                            "page": 1,
                        }
                    ]
            else:
                part["page"] += 1
            locked.progress = state
            locked.lease_until = timezone.now() + timedelta(minutes=10)
            locked.save(update_fields=["progress", "lease_until", "discovered"])
    SourceCheckRun.objects.filter(pk=run_id).update(status="queued")


def parse_document(data, url):
    soup = BeautifulSoup(decode_html(data), "html.parser")
    body = soup.select_one(
        "#UCAP-CONTENT, .TRS_Editor, .viewTRS_UEDITOR, .TRS_UEDITOR, "
        ".trs_editor_view, #zoom, .article-content, .article_con, .article-con, "
        ".zwxl-article, #detail_con, .detail_con, .content-main"
    )
    if body is None:
        raise SourceUnavailable("BODY_STRUCTURE_UNVERIFIED")
    nested = body.select_one(
        "#contentText, .article-content, .TRS_Editor, .viewTRS_UEDITOR, "
        ".TRS_UEDITOR, .trs_editor_view, .article_con, .article-con"
    )
    if nested is not None:
        body = nested
    text = readable_html_text(body)
    if len(text) < 30:
        raise SourceUnavailable("BODY_TOO_SHORT")
    attachments = {}
    # Only links inside the verified policy body are attachments. Site-wide
    # navigation and "related reading" links must never enter the download queue.
    for link in body.select("a[href]"):
        target = urldefrag(urljoin(url, link["href"]))[0]
        if (
            urlparse(target)
            .path.lower()
            .endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".wps", ".ofd", ".zip"))
        ):
            attachments[target] = link.get_text(" ", strip=True)
    return {"text": text, "attachments": attachments}
