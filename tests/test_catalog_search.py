from datetime import date, timedelta

import pytest
from accounts.models import Entitlement
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from policies.catalog import batch_state
from policies.extraction import extract_metadata
from policies.models import Opportunity, OpportunityBatch, Policy, PolicyRelation
from policies.services import fingerprint, update_validity
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_business_and_direction_filters_intersect(catalog):
    _, _, policies, _, client = catalog
    first, second = policies
    first.industry = second.industry = "water_environment"
    first.business_domains = ["water_supply"]
    first.direction_tags = ["ai"]
    second.business_domains = ["water_supply"]
    second.direction_tags = ["equipment_renewal"]
    first.save()
    second.save()
    response = client.post(
        "/api/v1/search",
        {"industry": "water_environment", "business_domain": "water_supply", "direction_tag": "ai"},
        format="json",
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(first.pk)]
    response = client.post(
        "/api/v1/search", {"business_domain": "sludge", "direction_tag": "ai"}, format="json"
    )
    assert response.json()["count"] == 0


@pytest.fixture
def catalog(db):
    cache.clear()
    admin = get_user_model().objects.create_superuser("catalog-admin")
    reader = get_user_model().objects.create_user("catalog-reader")
    policies = []
    for index in range(2):
        body = f"水务项目申报原文第{index}份。本办法自公布之日起施行。财政资金支持试点，申报时间须按批次通知执行。"
        policies.append(
            Policy.objects.create(
                title=f"水务政策{index}",
                issuer="测试机关",
                publication_date=date(2026, 1, index + 1),
                body=body,
                source_key=fingerprint(str(index)),
                content_hash=fingerprint(body),
                source_url=f"https://www.gov.cn/test/{index}",
                source_grade="L1",
                geographic_level="national",
                status="published",
                document_type="policy",
            )
        )
    p = policies[0]
    opportunity = Opportunity.objects.create(
        policy=p,
        title="水务项目支持",
        category="fiscal",
        status="open",
        evidence_policy=p,
        evidence_version=1,
        evidence_quote=p.body,
        verification_status="verified",
    )
    client = APIClient()
    client.force_authenticate(reader)
    return admin, reader, policies, opportunity, client


@pytest.mark.django_db
def test_customer_search_and_full_text_do_not_require_or_follow_subscriptions(catalog):
    from subscriptions.models import Subscription

    _, reader, policies, opportunity, client = catalog
    assert not Subscription.objects.filter(user=reader).exists()
    hidden_ids = []
    for index, changes in enumerate([
        {"status": "draft"},
        {"status": "excluded"},
        {"status": "withdrawn"},
        {"source_grade": "L4"},
        {"is_demo": True},
    ]):
        hidden = Policy.objects.create(
            title=f"水务政策隐藏{index}",
            publication_date=date(2026, 1, 1),
            body="水务政策非正式或非公开内容。",
            source_key=fingerprint(f"hidden-subscription-{index}"),
            content_hash=fingerprint(f"hidden-body-{index}"),
            source_url=f"https://www.gov.cn/test/hidden-{index}",
            **{"status": "published", "source_grade": "L1", **changes},
        )
        hidden_ids.append(hidden.pk)
    for with_subscription in (False, True):
        if with_subscription:
            Subscription.objects.create(
                user=reader, name="只关注另一主题", keywords="不匹配任何政策",
                idempotency_key="unrelated-subscription",
            )
        response = client.post("/api/v1/search", {"q": "水务"}, format="json")
        assert response.status_code == 200
        assert {item["id"] for item in response.json()["items"]} == {
            str(p.pk) for p in policies
        }
        for policy in policies:
            detail = client.get(f"/api/v1/policies/{policy.pk}")
            assert detail.status_code == 200
            assert detail.json()["body"] == policy.body
        result = client.post("/api/v1/search", {"view": "opportunity"}, format="json")
        assert result.status_code == 200
        assert [item["id"] for item in result.json()["items"]] == [str(opportunity.pk)]
        for pk in hidden_ids:
            assert client.get(f"/api/v1/policies/{pk}").status_code == 404


@pytest.mark.django_db
def test_search_metadata_scope_dates_and_source_previews(catalog):
    _, _, policies, _, client = catalog
    first, second = policies
    first.issuer = "南宁市水利局"
    first.summary = "节水改造支持政策"
    first.structured_keywords = [{"term": "循环利用"}]
    first.body = "背景说明。" * 100 + "重点支持节水设施改造。"
    first.save()
    for term in ("水利局", "节水改造", "循环利用"):
        response = client.post("/api/v1/search", {"q": term}, format="json")
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == [str(first.pk)]
    response = client.post("/api/v1/search", {"q": "水务 水利局"}, format="json")
    assert response.json()["count"] == 1  # Separate terms may match separate fields.
    response = client.post("/api/v1/search", {"q": "水利局", "scope": "title"}, format="json")
    assert response.json()["count"] == 0
    response = client.post("/api/v1/search", {"published_from": "2026-01-02", "published_to": "2026-01-02"}, format="json")
    assert [item["id"] for item in response.json()["items"]] == [str(second.pk)]
    response = client.post("/api/v1/search", {"q": "节水设施"}, format="json")
    preview = response.json()["previews"][str(first.pk)]
    assert "重点支持节水设施改造。" in preview["text"]
    assert preview["source"] == "正文片段"
    assert preview["matched_fields"] == ["正文"]
    for data in ({"published_from": "2026-02-02", "published_to": "2026-01-01"},
                 {"published_from": "not-a-date"}):
        assert client.post("/api/v1/search", data, format="json").status_code == 400


@pytest.mark.django_db
def test_search_title_ranks_above_body_and_hidden_policies_never_match(catalog):
    _, _, policies, _, client = catalog
    first, second = policies
    first.title = "节水改造"
    first.save()
    second.body = "节水改造。" * 100
    second.save()
    response = client.post("/api/v1/search", {"q": "节水改造", "sort": "relevance"}, format="json")
    assert [item["id"] for item in response.json()["items"]] == [str(first.pk), str(second.pk)]
    first.status = "draft"
    first.save()
    response = client.post("/api/v1/search", {"q": "节水改造"}, format="json")
    assert [item["id"] for item in response.json()["items"]] == [str(second.pk)]


def test_quoted_search_terms_and_keyword_limit():
    from policies.search import SearchRequest
    from policies.search_text import search_terms

    assert search_terms('水务 "南水 [2026] 10号" 水务') == ["水务", "南水 [2026] 10号"]
    serializer = SearchRequest(data={"q": "a b c d e f g h i"})
    assert not serializer.is_valid()


def batch(opportunity, **kwargs):
    p = opportunity.policy
    return OpportunityBatch.objects.create(
        opportunity=opportunity,
        name=kwargs.pop("name", "第一批"),
        evidence_policy=p,
        evidence_version=p.version,
        evidence_quote=p.body,
        verification_status="verified",
        **kwargs,
    )


@pytest.mark.django_db
def test_status_layers_deadline_boundaries_and_hints(catalog):
    _, _, policies, opportunity, _ = catalog
    now = timezone.now()
    current = batch(
        opportunity,
        status="open",
        starts_at=now - timedelta(days=2),
        deadline_at=now + timedelta(days=2),
    )
    assert batch_state(current, now) == ("open", True)
    assert batch_state(current, current.deadline_at) == ("closed", False)
    current.status = "suspended"
    assert batch_state(current, now) == ("suspended", False)
    current.status = "completed"
    assert batch_state(current, now) == ("completed", False)
    current.status = "ongoing"
    current.deadline_at = None
    assert batch_state(current, now) == ("ongoing", False)
    policies[0].refresh_from_db()
    opportunity.refresh_from_db()
    assert policies[0].validity_status == "unverified"
    assert policies[0].status == "published" and opportunity.status == "open"


@pytest.mark.django_db
def test_catalog_permissions_proof_and_dates(catalog):
    admin, _, policies, opportunity, client = catalog
    payload = {
        "opportunity": str(opportunity.pk),
        "name": "第二批",
        "status": "open",
        "evidence_policy": str(policies[0].pk),
        "evidence_version": 1,
        "evidence_quote": policies[0].body,
        "verification_status": "verified",
    }
    assert (
        client.post("/api/v1/admin/opportunity-batches", payload, format="json").status_code == 403
    )
    client.force_authenticate(admin)
    bad = {
        **payload,
        "starts_at": "2026-10-02T10:00:00+08:00",
        "deadline_at": "2026-10-01T10:00:00+08:00",
    }
    assert client.post("/api/v1/admin/opportunity-batches", bad, format="json").status_code == 400
    assert (
        client.post(
            "/api/v1/admin/opportunity-batches",
            {**payload, "evidence_quote": "无原文支持的事实"},
            format="json",
        ).status_code
        == 400
    )
    result = client.post("/api/v1/admin/opportunity-batches", payload, format="json")
    assert result.status_code == 201, result.data
    assert result.json()["verified_by"] == admin.pk


@pytest.mark.django_db
def test_directed_relationship_verified_and_version_bound(catalog):
    admin, reader, policies, _, client = catalog
    a, b = policies
    client.force_authenticate(admin)
    payload = {
        "from_policy": str(a.id),
        "to_policy": str(b.id),
        "kind": "implements",
        "evidence_policy": str(a.id),
        "evidence_version": 1,
        "evidence_quote": a.body,
        "verification_status": "pending",
    }
    result = client.post("/api/v1/admin/policy-relations", payload, format="json")
    assert result.status_code == 201, result.data
    relation_id = result.json()["id"]
    client.force_authenticate(reader)
    assert client.get(f"/api/v1/policies/{a.pk}").json()["relations"] == []
    client.force_authenticate(admin)
    assert (
        client.patch(
            f"/api/v1/admin/policy-relations/{relation_id}",
            {"verification_status": "verified"},
            format="json",
        ).status_code
        == 200
    )
    relation = PolicyRelation.objects.get()
    assert relation.from_policy == a and relation.to_policy == b
    client.force_authenticate(reader)
    assert len(client.get(f"/api/v1/policies/{a.pk}").json()["relations"]) == 1
    update_validity(a.pk, admin, 1, "effective", "本办法自公布之日起施行。")
    assert client.get(f"/api/v1/policies/{a.pk}").json()["relations"] == []
    assert client.get("/api/v1/opportunities").json()["count"] == 0


@pytest.mark.django_db
def test_search_views_filters_deadline_order_and_l4_exclusion(catalog):
    _, _, policies, opportunity, client = catalog
    now = timezone.now()
    batch(opportunity, status="open", deadline_at=now + timedelta(days=2))
    result = client.post(
        "/api/v1/search",
        {"view": "opportunity", "sort": "deadline", "category": "fiscal", "q": "水务"},
        format="json",
    )
    assert result.status_code == 200, result.data
    assert result.json()["count"] == 1
    assert result.json()["items"][0]["batches"][0]["closing_soon"] is True
    assert (
        client.post(
            "/api/v1/search", {"view": "policy", "sort": "deadline"}, format="json"
        ).status_code
        == 400
    )
    assert client.post(
        "/api/v1/search", {"view": "policy", "sort": "latest"}, format="json"
    ).json()["items"][0]["id"] == str(policies[1].pk)
    policies[0].source_grade = "L4"
    policies[0].save(update_fields=["source_grade"])
    assert (
        client.post("/api/v1/search", {"view": "opportunity"}, format="json").json()["count"] == 0
    )


@pytest.mark.django_db
def test_natural_query_retrieves_database_before_summarizing(catalog, monkeypatch, settings):
    _, reader, policies, _, client = catalog
    settings.AI_API_KEY, settings.AI_MODEL, settings.AI_BASE_URL = (
        "fake",
        "fake",
        "https://example.invalid",
    )
    calls = []

    def intent(question):
        calls.append("intent")
        return {"keywords": ["水务"], "category": "fiscal", "opportunity_status": "open"}

    def summary(question, evidence):
        calls.append("summary")
        assert str(policies[0].pk) in evidence
        return {
            "claims": [
                {
                    "text": "模型虚构的补助额度不会展示",
                    "evidence_id": str(policies[0].pk),
                    "quote": "本办法自公布之日起施行。",
                }
            ]
        }

    monkeypatch.setattr("policies.search.parse_intent", intent)
    monkeypatch.setattr("policies.search.generate", summary)
    response = client.post(
        "/api/v1/search", {"mode": "natural", "q": "查询水务政策"}, format="json"
    )
    assert response.status_code == 200, response.data
    assert calls == ["intent", "summary"]
    assert response.json()["applied_filters"]["category"] == ""
    assert response.json()["applied_filters"]["opportunity_status"] == ""
    assert response.json()["claims"][0]["text"] == "本办法自公布之日起施行。"
    assert "模型虚构" not in str(response.json())
    Entitlement.objects.create(user=reader, capability="policy_search", allowed=False)
    assert (
        client.post("/api/v1/search", {"mode": "natural", "q": "水务"}, format="json").status_code
        == 403
    )


def test_natural_intent_drops_unstated_filters_and_generic_keywords():
    from policies.search import sanitize_intent

    result = sanitize_intent(
        "南宁市有哪些污水处理相关政策？",
        {
            "keywords": ["南宁", "污水处理", "政策", "相关"],
            "topic": "水务",
            "validity_status": "not_effective",
            "opportunity_status": "open",
            "business_domain": "urban_sewage",
        },
    )

    assert result["keywords"] == ["南宁", "污水处理"]
    assert result["topic"] == ""
    assert result["validity_status"] == ""
    assert result["opportunity_status"] == ""
    assert result["business_domain"] == "urban_sewage"


@pytest.mark.django_db
def test_natural_city_in_province_is_repaired_without_widening_or_overriding_user(catalog, monkeypatch, settings):
    from policies.search import sanitize_intent

    _, _, policies, _, client = catalog
    first, second = policies
    for policy, city in ((first, "南宁市"), (second, "梧州市")):
        policy.province = "广西壮族自治区"
        policy.city = city
        policy.geographic_level = "city"
        policy.topics = ["水务"]
        policy.body = "支持污水处理设施建设。"  # Does not literally contain 水务 or the city.
        policy.save()
    settings.AI_API_KEY, settings.AI_MODEL, settings.AI_BASE_URL = "fake", "fake", "https://example.invalid"
    monkeypatch.setattr("policies.search.parse_intent", lambda question: sanitize_intent(question, {
        "province": "南宁市", "city": "", "topic": "水务", "geographic_level": "city",
        "keywords": ["南宁", "水务", "目前", "政策"],
    }))
    monkeypatch.setattr("policies.search.generate", lambda *args: {"claims": []})
    params = {"mode": "natural", "q": "南宁目前有哪些水务相关政策"}
    response = client.post("/api/v1/search", params, format="json")
    assert response.status_code == 200
    result = response.json()
    assert [p["id"] for p in result["items"]] == [str(first.pk)]
    assert result["applied_filters"]["province"] == ""
    assert result["applied_filters"]["city"] == "南宁市"
    assert result["applied_filters"]["geographic_level"] == ""
    assert result["keywords"] == []
    # Explicit form selections remain authoritative even if they differ from AI.
    response = client.post("/api/v1/search", {**params, "city": "梧州市"}, format="json")
    assert [p["id"] for p in response.json()["items"]] == [str(second.pk)]


@pytest.mark.django_db
def test_natural_geography_aliases_and_unknown_regions(catalog):
    from policies.search import normalize_intent_geography

    _, user, policies, _, _ = catalog
    policies[0].province = "广西壮族自治区"
    policies[0].city = "南宁市"
    policies[0].save()
    fixed = normalize_intent_geography(user, "广西南宁污水处理政策", {
        "province": "广西", "city": "南宁", "keywords": ["南宁", "污水处理"]})
    assert fixed["province"] == "广西壮族自治区"
    assert fixed["city"] == "南宁市"
    assert fixed["keywords"] == ["污水处理"]
    broad = normalize_intent_geography(user, "南宁目前有哪些水务相关政策", {
        "city": "南宁市", "industry": "water_environment", "keywords": ["水务"]})
    assert broad["topic"] == "水务"
    assert broad["keywords"] == []
    unknown = normalize_intent_geography(user, "三亚污水政策", {
        "city": "三亚市", "keywords": ["污水"]})
    assert unknown["city"] == "三亚市"


@pytest.mark.django_db
def test_natural_empty_db_and_invalid_citation_fail_closed(catalog, monkeypatch, settings):
    _, _, _, _, client = catalog
    settings.AI_API_KEY, settings.AI_MODEL, settings.AI_BASE_URL = (
        "fake",
        "fake",
        "https://example.invalid",
    )
    monkeypatch.setattr(
        "policies.search.parse_intent", lambda q: {"keywords": ["完全不存在的资料"]}
    )

    def forbidden(*args):
        raise AssertionError("No generation without evidence")

    monkeypatch.setattr("policies.search.generate", forbidden)
    result = client.post("/api/v1/search", {"mode": "natural", "q": "未知政策"}, format="json")
    assert result.status_code == 200 and result.json()["claims"] == []
    monkeypatch.setattr("policies.search.parse_intent", lambda q: {"keywords": ["水务"]})
    monkeypatch.setattr(
        "policies.search.generate",
        lambda *args: {
            "claims": [{"text": "虚构", "evidence_id": "fake", "quote": "不存在的原文"}]
        },
    )
    invalid = client.post("/api/v1/search", {"mode": "natural", "q": "水务"}, format="json")
    assert invalid.status_code == 200
    assert invalid.json()["claims"] == []
    settings.AI_API_KEY = ""
    assert (
        client.post("/api/v1/search", {"mode": "natural", "q": "水务"}, format="json").status_code
        == 503
    )


def test_extraction_keeps_anchored_terms_and_literal_excerpt():
    body = "水务申报给予财政资金支持。政策文号：测试〔2026〕1号。"
    extracted = extract_metadata(body)
    assert extracted["summary"] in body
    assert extracted["structured_keywords"]
    for keyword in extracted["structured_keywords"]:
        assert body[keyword["start"] : keyword["end"]] == keyword["term"]
