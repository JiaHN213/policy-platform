"""Validated, resumable adapter for the complete State Council policy catalogue."""

import hashlib
import ipaddress
import json
import socket
import time
from datetime import date, timedelta
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from core.storage import store_original
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import CrawlPage, DiscoveredItem, SourceCheckRun

GOV_LIBRARY_URL = "https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary"
ALLOWED_HOSTS = {"sousuo.www.gov.cn", "www.gov.cn"}


class SourceUnavailable(Exception):
    pass


def validate_url(url, resolve=True):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise SourceUnavailable("URL_NOT_ALLOWED")
    if resolve:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise SourceUnavailable("NON_PUBLIC_ADDRESS")


def fetch_resource(url, accepted_types=("text/html",), max_bytes=5_000_000):
    # Strict host allowlist, per-redirect validation, bounded body and timeout.
    # Production egress rules must additionally prevent DNS rebinding to private networks.
    for _ in range(4):
        validate_url(url)
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            with client.stream(
                "GET",
                url,
                headers={"User-Agent": "PolicyObserver/0.1 (policy source verification)"},
            ) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0].lower()
                if content_type not in accepted_types:
                    raise SourceUnavailable("UNEXPECTED_CONTENT_TYPE")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise SourceUnavailable("BODY_TOO_LARGE")
                    chunks.append(chunk)
                return b"".join(chunks), content_type, url
    raise SourceUnavailable("TOO_MANY_REDIRECTS")


def fetch_html(url):
    return fetch_resource(url)[0]


def parse_list_page(payload, category, page):
    """Validate the public site's 1-based, separate-category response."""
    if category not in {"gw", "bm"} or type(page) is not int or page < 1:
        raise ValueError("Invalid category or page")
    try:
        params = payload["paramsVO"]
        result = payload["searchVO"]
        if str(payload["code"]) != "200" or params["t"] != f"zhengcelibrary_{category}":
            raise ValueError("Unexpected response")
        if int(params["p"]) != page or params["sort"] != "pubtime":
            raise ValueError("Unexpected page or ordering")
        total = int(result["totalCount"])
        rows = result["listVO"]
        if total < 0 or not isinstance(rows, list) or len(rows) > 5:
            raise ValueError("Invalid rows")
        if not rows and (page - 1) * 5 < total:
            raise ValueError("Unexpected empty page")
        items = []
        for row in rows:
            url = urldefrag(row["url"])[0]
            validate_url(url, resolve=False)
            if urlparse(url).hostname != "www.gov.cn" or "/zhengce/" not in urlparse(url).path:
                raise ValueError("Unexpected policy URL")
            title = BeautifulSoup(row["title"], "html.parser").get_text(" ", strip=True)
            if not title or len(title) > 500:
                raise ValueError("Invalid title")
            items.append(
                {
                    "url": url,
                    "title": title,
                    "publication_date": date.fromisoformat(row["pubtimeStr"].replace(".", "-")),
                    "issuer": row.get("puborg") or "",
                    "document_number": row.get("pcode") or "",
                }
            )
        if len({item["url"] for item in items}) != len(items):
            raise ValueError("Duplicate page rows")
        return {"items": items, "total": total, "page": page}
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SourceUnavailable("LIST_STRUCTURE_UNVERIFIED") from exc


def fetch_list_page(category, page):
    if category not in {"gw", "bm"} or type(page) is not int or not 1 <= page <= 10000:
        raise ValueError("Invalid category or page")
    request = httpx.Request(
        "GET",
        "https://sousuo.www.gov.cn/search-gov/data",
        params={
            "t": f"zhengcelibrary_{category}",
            "q": "",
            "sort": "pubtime",
            "sortType": 1,
            "searchfield": "title",
            "p": page,
            "n": 5,
            "type": "gwyzcwjk",
        },
    )
    data, _, _ = fetch_resource(str(request.url), ("application/json",))
    try:
        return {**parse_list_page(json.loads(data), category, page), "raw": data}
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceUnavailable("LIST_STRUCTURE_UNVERIFIED") from exc


def new_progress():
    return {
        "category_index": 0,
        "categories": [
            {"code": "gw", "page": 1, "total": None, "last_hash": ""},
            {"code": "bm", "page": 1, "total": None, "last_hash": ""},
        ],
        "pages_scanned": 0,
        "rows_scanned": 0,
    }


def advance_full_scan(run_id, budget=5, fetcher=None):
    """Scan the complete central policy catalogue in bounded, resumable slices."""
    fetcher = fetcher or fetch_list_page
    run = SourceCheckRun.objects.select_related("source").get(pk=run_id)
    state = dict(run.progress or new_progress())
    state.setdefault("categories", new_progress()["categories"])
    state.setdefault("category_index", 0)
    state.setdefault("pages_scanned", 0)
    state.setdefault("rows_scanned", 0)

    for request_index in range(budget):
        category_index = int(state["category_index"])
        if category_index >= len(state["categories"]):
            break
        part = state["categories"][category_index]
        category, page = part["code"], int(part["page"])
        result = fetcher(category, page)
        identity = hashlib.sha256(
            json.dumps([item["url"] for item in result["items"]], sort_keys=True).encode()
        ).hexdigest()
        if page > 1 and result["items"] and identity == part.get("last_hash"):
            raise SourceUnavailable("PAGINATION_REPEATED")
        original = store_original(result.get("raw", b""), "application/json")

        with transaction.atomic():
            locked = SourceCheckRun.objects.select_for_update().select_related("source").get(
                pk=run_id
            )
            if locked.status != "running":
                return
            for item in result["items"]:
                metadata = {
                    **item,
                    "publication_date": item["publication_date"].isoformat(),
                }
                discovered, created = DiscoveredItem.objects.get_or_create(
                    source=locked.source,
                    url=item["url"],
                    defaults={"title": item["title"], "metadata": metadata},
                )
                if not discovered.metadata:
                    DiscoveredItem.objects.filter(pk=discovered.pk).update(metadata=metadata)
                locked.discovered += int(created)
            CrawlPage.objects.update_or_create(
                run=locked,
                query=f"central:{category}",
                page=page,
                defaults={
                    "total": result["total"],
                    "item_count": len(result["items"]),
                    "response_hash": identity,
                    "object_key": original["object_key"],
                },
            )
            part["total"] = result["total"]
            part["last_hash"] = identity
            state["pages_scanned"] += 1
            state["rows_scanned"] += len(result["items"])
            if not result["items"] or page * 5 >= result["total"]:
                state["category_index"] = category_index + 1
            else:
                part["page"] = page + 1
            locked.progress = state
            locked.lease_until = timezone.now() + timedelta(minutes=10)
            locked.save(update_fields=["progress", "discovered", "lease_until"])
        if request_index + 1 < budget:
            time.sleep(max(1.0, min(5.0, settings.POLICY_CRAWL_MIN_INTERVAL_SECONDS)))

    with transaction.atomic():
        locked = SourceCheckRun.objects.select_for_update().select_related("source").get(pk=run_id)
        if int(state["category_index"]) >= len(state["categories"]):
            now = timezone.now()
            locked.status = "succeeded"
            locked.finished_at = now
            locked.lease_until = None
            locked.error_code = locked.error_message = ""
            locked.source.last_success_at = now
            locked.source.verification_status = "verified"
            locked.source.save(update_fields=["last_success_at", "verification_status"])
            locked.save(
                update_fields=[
                    "status",
                    "progress",
                    "discovered",
                    "finished_at",
                    "lease_until",
                    "error_code",
                    "error_message",
                ]
            )
        else:
            locked.status = "queued"
            locked.lease_until = timezone.now() + timedelta(minutes=10)
            locked.save(update_fields=["status", "progress", "discovered", "lease_until"])


def discover_links(html, base_url=GOV_LIBRARY_URL):
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    for link in soup.select("a[href]"):
        url = urldefrag(urljoin(base_url, link["href"]))[0]
        title = link.get_text(" ", strip=True)
        parsed = urlparse(url)
        if (
            parsed.hostname == "www.gov.cn"
            and "/zhengce/" in parsed.path
            and parsed.path.endswith(".htm")
            and len(title) >= 8
        ):
            validate_url(url, resolve=False)
            found[url] = {"url": url, "title": title[:500]}
    if not found:
        # An empty JS shell or access challenge is not evidence of "no new policies".
        raise SourceUnavailable("LIST_STRUCTURE_UNVERIFIED")
    return list(found.values())
