from datetime import date

import pytest
from django.contrib.auth import get_user_model
from policies.models import Policy
from policies.opensearch import OpenSearchUnavailable, SearchHits, policy_document
from policies.services import fingerprint
from rest_framework.test import APIClient


@pytest.fixture
def opensearch_catalog(db):
    reader = get_user_model().objects.create_user("opensearch-reader")
    policies = []
    for index in range(2):
        body = f"水务项目申报原文第{index}份，支持智慧水务建设。"
        policies.append(
            Policy.objects.create(
                title=f"智慧水务政策{index}",
                issuer="测试机关",
                publication_date=date(2026, 1, index + 1),
                body=body,
                source_key=fingerprint(f"opensearch-{index}"),
                content_hash=fingerprint(body),
                source_url=f"https://www.gov.cn/opensearch/{index}",
                source_grade="L1",
                geographic_level="national",
                status="published",
                document_type="policy",
            )
        )
    client = APIClient()
    client.force_authenticate(reader)
    return policies, client


@pytest.mark.django_db
def test_keyword_search_uses_opensearch_order_then_postgres_authority(
    opensearch_catalog, monkeypatch, settings
):
    policies, client = opensearch_catalog
    first, second = policies
    settings.OPENSEARCH_ENABLED = True
    monkeypatch.setattr(
        "policies.search.search_policy_ids",
        lambda *args, **kwargs: SearchHits(ids=[str(second.pk), str(first.pk)], total=2),
    )
    response = client.post("/api/v1/search", {"q": "水务"}, format="json")
    assert response.status_code == 200
    assert response.json()["search_backend"] == "opensearch"
    assert [item["id"] for item in response.json()["items"]] == [
        str(second.pk),
        str(first.pk),
    ]


@pytest.mark.django_db
def test_search_falls_back_when_opensearch_is_unavailable(
    opensearch_catalog, monkeypatch, settings
):
    _, client = opensearch_catalog
    settings.OPENSEARCH_ENABLED = True

    def unavailable(*args, **kwargs):
        raise OpenSearchUnavailable("test outage")

    monkeypatch.setattr("policies.search.search_policy_ids", unavailable)
    response = client.post("/api/v1/search", {"q": "水务"}, format="json")
    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert response.json()["search_backend"] == "postgresql_fallback"


@pytest.mark.django_db
def test_search_rechecks_database_when_opensearch_index_is_empty(
    opensearch_catalog, monkeypatch, settings
):
    _, client = opensearch_catalog
    settings.OPENSEARCH_ENABLED = True
    monkeypatch.setattr(
        "policies.search.search_policy_ids",
        lambda *args, **kwargs: SearchHits(ids=[], total=0),
    )

    response = client.post("/api/v1/search", {"q": "水务"}, format="json")

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert response.json()["search_backend"] == "postgresql_fallback"


@pytest.mark.django_db
def test_policy_document_extracts_keyword_terms(opensearch_catalog):
    policies, _ = opensearch_catalog
    policy = policies[0]
    policy.structured_keywords = [
        {"term": "污水处理", "start": 0, "end": 4, "category": "domain"},
        "设备更新",
    ]
    policy.summary = "水务项目政策摘要"
    policy.publication_date = date(2026, 1, 1)
    document = policy_document(policy)
    assert document["structured_keywords"] == ["污水处理", "设备更新"]
    assert document["summary"] == "水务项目政策摘要"
    assert document["source_url"].startswith("https://www.gov.cn/")
    assert fingerprint(document["body"])


def test_opensearch_query_keeps_business_filters(monkeypatch, settings):
    from policies import opensearch

    settings.OPENSEARCH_ENABLED = True
    captured = {}

    def request(method, path, **kwargs):
        captured.update(kwargs["json"])
        return {
            "hits": {
                "total": {"value": 1, "relation": "eq"},
                "hits": [{"_id": "00000000-0000-0000-0000-000000000001"}],
            }
        }

    monkeypatch.setattr(opensearch, "_request", request)
    result = opensearch.search_policy_ids(
        "智慧水务",
        {
            "sort": "latest",
            "business_domain": "water_supply",
            "direction_tag": "ai",
            "source_grade": "L1",
        },
        include_demo=False,
        page=2,
    )
    assert result.total == 1
    assert captured["from"] == 20
    filters = captured["query"]["bool"]["filter"]
    assert {"term": {"business_domains": "water_supply"}} in filters
    assert {"term": {"direction_tags": "ai"}} in filters
    assert {"term": {"is_demo": False}} in filters


def test_opensearch_terms_allow_cross_field_matches_and_natural_or(monkeypatch):
    from policies import opensearch

    captured = {}
    def request(method, path, **kwargs):
        captured.update(kwargs["json"])
        return {"hits": {"total": 0, "hits": []}}
    monkeypatch.setattr(opensearch, "_request", request)
    opensearch.search_policy_ids("水务 申报", {"published_from": "2026-01-01"}, include_demo=False, page=1)
    query = captured["query"]["bool"]
    assert {"range": {"publication_date": {"gte": "2026-01-01"}}} in query["filter"]
    groups = query["must"][0]["bool"]["must"]
    assert len(groups) == 2
    assert any("match_phrase" in clause for clause in groups[0]["dis_max"]["queries"])
    opensearch.search_policy_ids("水务 申报", {"scope": "title"}, include_demo=False, page=1, require_all_terms=False)
    query = captured["query"]["bool"]["must"][0]["bool"]
    assert query["minimum_should_match"] == 1
    assert len(query["should"]) == 2
    assert all(len(group["dis_max"]["queries"]) == 1 for group in query["should"])
