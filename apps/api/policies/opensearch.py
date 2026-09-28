import json
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Policy, SearchIndexState
from .search_text import SEARCH_FIELDS, search_terms


class OpenSearchUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchHits:
    ids: list[str]
    total: int


def configured():
    return bool(settings.OPENSEARCH_ENABLED and settings.OPENSEARCH_URL)


def _url(path=""):
    return settings.OPENSEARCH_URL.rstrip("/") + "/" + path.lstrip("/")


def _index_path(suffix=""):
    name = quote(settings.OPENSEARCH_INDEX, safe="-_.")
    return f"{name}/{suffix.lstrip('/')}" if suffix else name


def _client():
    auth = None
    if settings.OPENSEARCH_USERNAME:
        auth = (settings.OPENSEARCH_USERNAME, settings.OPENSEARCH_PASSWORD)
    return httpx.Client(
        timeout=settings.OPENSEARCH_TIMEOUT_SECONDS,
        verify=settings.OPENSEARCH_VERIFY_CERTS,
        auth=auth,
        follow_redirects=False,
        trust_env=False,
    )


def _request(method, path, *, allowed=(200,), **kwargs):
    if not configured():
        raise OpenSearchUnavailable("OPENSEARCH_DISABLED")
    try:
        with _client() as client:
            response = client.request(method, _url(path), **kwargs)
    except httpx.HTTPError as exc:
        raise OpenSearchUnavailable("OPENSEARCH_CONNECTION_FAILED") from exc
    if response.status_code not in allowed:
        raise OpenSearchUnavailable(f"OPENSEARCH_HTTP_{response.status_code}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise OpenSearchUnavailable("OPENSEARCH_INVALID_RESPONSE") from exc


INDEX_DEFINITION = {
    "settings": {
        "index": {
            "max_ngram_diff": 2,
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "analysis": {
            "tokenizer": {
                "policy_zh_ngram": {
                    "type": "ngram",
                    "min_gram": 1,
                    "max_gram": 3,
                    "token_chars": ["letter", "digit"],
                }
            },
            "analyzer": {
                "policy_zh_index": {
                    "type": "custom",
                    "tokenizer": "policy_zh_ngram",
                    "filter": ["lowercase"],
                },
                "policy_zh_search": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase"],
                },
            },
        },
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "id": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "policy_zh_index",
                "search_analyzer": "policy_zh_search",
                "fields": {"raw": {"type": "keyword", "ignore_above": 500}},
            },
            "document_number": {
                "type": "text",
                "analyzer": "policy_zh_index",
                "search_analyzer": "policy_zh_search",
            },
            "issuer": {
                "type": "text",
                "analyzer": "policy_zh_index",
                "search_analyzer": "policy_zh_search",
                "fields": {"raw": {"type": "keyword", "ignore_above": 200}},
            },
            "publication_date": {"type": "date"},
            "body": {
                "type": "text",
                "analyzer": "policy_zh_search",
                "search_analyzer": "policy_zh_search",
            },
            "summary": {
                "type": "text",
                "analyzer": "policy_zh_index",
                "search_analyzer": "policy_zh_search",
            },
            "structured_keywords": {"type": "keyword", "ignore_above": 100},
            "topics": {"type": "keyword"},
            "industry": {"type": "keyword"},
            "business_domains": {"type": "keyword"},
            "direction_tags": {"type": "keyword"},
            "document_type": {"type": "keyword"},
            "geographic_level": {"type": "keyword"},
            "source_grade": {"type": "keyword"},
            "province": {"type": "keyword"},
            "city": {"type": "keyword"},
            "region": {"type": "keyword"},
            "validity_status": {"type": "keyword"},
            "status": {"type": "keyword"},
            "is_demo": {"type": "boolean"},
            "version": {"type": "integer"},
            "source_url": {"type": "keyword", "ignore_above": 2000},
            "updated_at": {"type": "date"},
        },
    },
}


def ensure_index(rebuild=False):
    path = _index_path()
    if rebuild:
        _request("DELETE", path, allowed=(200, 404))
    _request("HEAD", path, allowed=(200, 404))
    # A HEAD response has no JSON body, so perform a lightweight settings lookup
    # to distinguish an existing index from the 404 case.
    if not rebuild:
        try:
            _request("GET", path, allowed=(200,))
            return False
        except OpenSearchUnavailable as exc:
            if str(exc) != "OPENSEARCH_HTTP_404":
                raise
    _request("PUT", path, allowed=(200,), json=INDEX_DEFINITION)
    return True


def _strings(value):
    return [str(item)[:100] for item in (value or []) if str(item).strip()]


def _keywords(value):
    result = []
    for item in value or []:
        term = item.get("term", "") if isinstance(item, dict) else item
        if str(term).strip():
            result.append(str(term)[:100])
    return result


def policy_document(policy):
    return {
        "id": str(policy.pk),
        "title": policy.title,
        "document_number": policy.document_number,
        "issuer": policy.issuer,
        "publication_date": policy.publication_date.isoformat(),
        "body": policy.body,
        "summary": policy.summary,
        "structured_keywords": _keywords(policy.structured_keywords),
        "topics": _strings(policy.topics),
        "industry": policy.industry,
        "business_domains": _strings(policy.business_domains),
        "direction_tags": _strings(policy.direction_tags),
        "document_type": policy.document_type,
        "geographic_level": policy.geographic_level,
        "source_grade": policy.source_grade,
        "province": policy.province,
        "city": policy.city,
        "region": policy.region,
        "validity_status": policy.validity_status,
        "status": policy.status,
        "is_demo": policy.is_demo,
        "version": policy.version,
        "source_url": policy.source_url,
        "updated_at": policy.updated_at.isoformat(),
    }


def _bulk(policies):
    lines = []
    for policy in policies:
        document_id = str(policy.pk)
        eligible = (
            policy.status == Policy.Status.PUBLISHED
            and policy.source_grade in Policy.FORMAL_SOURCE_GRADES
        )
        if eligible:
            lines.append(
                json.dumps(
                    {"index": {"_index": settings.OPENSEARCH_INDEX, "_id": document_id}}
                )
            )
            lines.append(json.dumps(policy_document(policy), ensure_ascii=False))
        else:
            lines.append(
                json.dumps(
                    {"delete": {"_index": settings.OPENSEARCH_INDEX, "_id": document_id}}
                )
            )
    if not lines:
        return
    payload = "\n".join(lines) + "\n"
    result = _request(
        "POST",
        "_bulk?refresh=false",
        allowed=(200,),
        content=payload.encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
    )
    failures = []
    for item in result.get("items", []):
        operation = next(iter(item.values()))
        if operation.get("error") and operation.get("status") != 404:
            failures.append(operation.get("error", {}).get("reason", "bulk operation failed"))
    if failures:
        raise OpenSearchUnavailable("OPENSEARCH_BULK_FAILED: " + failures[0][:300])


def _bulk_delete_ids(document_ids):
    lines = [
        json.dumps(
            {"delete": {"_index": settings.OPENSEARCH_INDEX, "_id": document_id}}
        )
        for document_id in document_ids
    ]
    if not lines:
        return
    result = _request(
        "POST",
        "_bulk?refresh=false",
        allowed=(200,),
        content=("\n".join(lines) + "\n").encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
    )
    failures = []
    for item in result.get("items", []):
        operation = next(iter(item.values()))
        if operation.get("error") and operation.get("status") != 404:
            failures.append(operation.get("error", {}).get("reason", "bulk delete failed"))
    if failures:
        raise OpenSearchUnavailable("OPENSEARCH_BULK_FAILED: " + failures[0][:300])


def _indexed_policy_ids():
    """Return every indexed policy id without loading policy documents."""
    result_ids = set()
    search_after = None
    while True:
        payload = {
            "size": 1000,
            "_source": False,
            "query": {"match_all": {}},
            "sort": [{"id": {"order": "asc"}}],
        }
        if search_after is not None:
            payload["search_after"] = search_after
        result = _request(
            "POST", _index_path("_search"), allowed=(200,), json=payload
        )
        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            break
        result_ids.update(str(item["_id"]) for item in hits)
        if len(hits) < payload["size"]:
            break
        search_after = hits[-1].get("sort")
        if not search_after:
            raise OpenSearchUnavailable("OPENSEARCH_INVENTORY_MISSING_SORT")
    return result_ids


def _reconcile_index_ids():
    """Repair missing and obsolete documents when cursor-based sync missed a write."""
    eligible = Policy.objects.filter(
        status=Policy.Status.PUBLISHED,
        source_grade__in=Policy.FORMAL_SOURCE_GRADES,
    )
    eligible_ids = {str(value) for value in eligible.values_list("id", flat=True)}
    indexed_ids = _indexed_policy_ids()
    missing_ids = eligible_ids - indexed_ids
    obsolete_ids = indexed_ids - eligible_ids

    batch = []
    for policy in eligible.filter(pk__in=missing_ids).iterator(chunk_size=200):
        batch.append(policy)
        if len(batch) == 200:
            _bulk(batch)
            batch = []
    _bulk(batch)
    for offset in range(0, len(obsolete_ids), 200):
        _bulk_delete_ids(list(obsolete_ids)[offset : offset + 200])
    return len(missing_ids), len(obsolete_ids)


def sync_policy(policy_id):
    """Apply the current database state; an old event never restores an old document."""
    ensure_index()
    with transaction.atomic():
        policy = Policy.objects.select_for_update().get(pk=policy_id)
        _bulk([policy])
        _request("POST", _index_path("_refresh"), allowed=(200,))
        return {"version": policy.version, "message": "已同步当前政策版本；非公开政策已从索引移除。"}


def sync_index(*, rebuild=False):
    if not configured():
        raise OpenSearchUnavailable("OPENSEARCH_DISABLED")
    now = timezone.now()
    with transaction.atomic():
        state, _ = SearchIndexState.objects.select_for_update().get_or_create(
            singleton_key="default"
        )
        stale = not state.last_attempted_at or state.last_attempted_at < now - timedelta(minutes=30)
        if state.status == SearchIndexState.Status.SYNCING and not stale:
            return status()
        state.status = SearchIndexState.Status.SYNCING
        state.last_attempted_at = now
        state.last_error = ""
        state.save(update_fields=["status", "last_attempted_at", "last_error", "updated_at"])
        previous_sync = state.last_synced_at
    try:
        created = ensure_index(rebuild=rebuild)
        full = rebuild or created or not previous_sync
        queryset = Policy.objects.filter(updated_at__lte=now).order_by("updated_at", "id")
        if not full:
            queryset = queryset.filter(updated_at__gt=previous_sync)
        batch = []
        for policy in queryset.iterator(chunk_size=200):
            batch.append(policy)
            if len(batch) == 200:
                _bulk(batch)
                batch = []
        _bulk(batch)
        _request("POST", _index_path("_refresh"), allowed=(200,))
        count_result = _request("GET", _index_path("_count"), allowed=(200,))
        indexed_count = int(count_result.get("count", 0))
        expected_count = Policy.objects.filter(
            status=Policy.Status.PUBLISHED,
            source_grade__in=Policy.FORMAL_SOURCE_GRADES,
        ).count()
        # The timestamp cursor is fast, while this count check makes it safe. Only
        # fetch the complete id inventory when the two stores actually disagree.
        if indexed_count != expected_count:
            _reconcile_index_ids()
            _request("POST", _index_path("_refresh"), allowed=(200,))
            count_result = _request("GET", _index_path("_count"), allowed=(200,))
            indexed_count = int(count_result.get("count", 0))
            if indexed_count != expected_count:
                raise OpenSearchUnavailable(
                    f"OPENSEARCH_COUNT_MISMATCH: expected {expected_count}, got {indexed_count}"
                )
        SearchIndexState.objects.filter(singleton_key="default").update(
            status=SearchIndexState.Status.IDLE,
            last_synced_at=now,
            indexed_count=indexed_count,
            last_error="",
        )
        return status(check_cluster=False)
    except Exception as exc:
        SearchIndexState.objects.filter(singleton_key="default").update(
            status=SearchIndexState.Status.FAILED,
            last_error=str(exc)[:500],
        )
        raise


def search_policy_ids(query, params, *, include_demo, page, page_size=20,
                      terms=None, require_all_terms=True):
    filters = [
        {"term": {"status": Policy.Status.PUBLISHED}},
        {"terms": {"source_grade": list(Policy.FORMAL_SOURCE_GRADES)}},
    ]
    if not include_demo:
        filters.append({"term": {"is_demo": False}})
    field_map = {
        "industry": "industry",
        "business_domain": "business_domains",
        "direction_tag": "direction_tags",
        "document_type": "document_type",
        "geographic_level": "geographic_level",
        "source_grade": "source_grade",
        "province": "province",
        "city": "city",
        "region": "region",
        "validity_status": "validity_status",
        "topic": "topics",
    }
    for parameter, field in field_map.items():
        if params.get(parameter):
            filters.append({"term": {field: params[parameter]}})
    dates = {}
    if params.get("published_from"):
        dates["gte"] = params["published_from"]
    if params.get("published_to"):
        dates["lte"] = params["published_to"]
    if dates:
        filters.append({"range": {"publication_date": dates}})
    scope = params.get("scope", "all")
    fields = SEARCH_FIELDS if scope == "all" else {scope: SEARCH_FIELDS[scope]}
    clauses = []
    for term in (terms if terms is not None else search_terms(query))[:8]:
        matches = []
        for field, weight in fields.items():
            # Use matching n-grams for metadata, including adjacent Chinese pairs/triples.
            # The old standard query analyzer required scattered single characters only.
            if field == "body":
                matches.append({"match_phrase": {field: {"query": term, "boost": weight}}})
            else:
                matches.append({"match": {field: {
                    "query": term, "analyzer": "policy_zh_index", "operator": "and",
                    "boost": weight,
                }}})
        if scope == "all":
            matches.append({"term": {"structured_keywords": {"value": term, "boost": 6}}})
        clauses.append({"dis_max": {"queries": matches, "tie_breaker": 0.2}})
    text_query = {"bool": {"must": clauses}} if require_all_terms else {
        "bool": {"should": clauses, "minimum_should_match": 1}
    }
    must = [text_query] if clauses else []
    boosts = []
    if scope in {"all", "title"}:
        boosts.append({"term": {"title.raw": {"value": query, "boost": 60}}})
    sort = params.get("sort", "comprehensive")
    if sort == "latest":
        ordering = [
            {"publication_date": {"order": "desc", "unmapped_type": "date"}},
            {"_score": "desc"},
            {"id": "asc"},
        ]
    elif sort == "relevance":
        ordering = [{"_score": "desc"}, {"id": "asc"}]
    else:
        ordering = [
            {"_score": "desc"},
            {"publication_date": {"order": "desc", "unmapped_type": "date"}},
            {"id": "asc"},
        ]
    result = _request(
        "POST",
        _index_path("_search"),
        allowed=(200,),
        json={
            "from": (page - 1) * page_size,
            "size": page_size,
            "track_total_hits": True,
            "_source": False,
            "query": {"bool": {"must": must, "filter": filters, "should": boosts}},
            "sort": ordering,
        },
    )
    hits = result.get("hits", {})
    total = hits.get("total", 0)
    if isinstance(total, dict):
        total = total.get("value", 0)
    return SearchHits(ids=[item["_id"] for item in hits.get("hits", [])], total=int(total))


def status(*, check_cluster=True):
    state = SearchIndexState.objects.filter(singleton_key="default").first()
    result = {
        "enabled": configured(),
        "available": False,
        "backend": "postgresql_fallback",
        "index": settings.OPENSEARCH_INDEX,
        "sync_status": state.status if state else "not_started",
        "indexed_count": state.indexed_count if state else 0,
        "last_synced_at": state.last_synced_at if state else None,
        "last_attempted_at": state.last_attempted_at if state else None,
        "last_error": state.last_error if state else "",
    }
    if not configured():
        return result
    if not check_cluster:
        result.update({"available": True, "backend": "opensearch"})
        return result
    try:
        health = _request("GET", "_cluster/health", allowed=(200,))
        result.update(
            {
                "available": True,
                "backend": "opensearch",
                "cluster_status": health.get("status", "unknown"),
            }
        )
    except OpenSearchUnavailable as exc:
        result["connection_error"] = str(exc)
    return result
