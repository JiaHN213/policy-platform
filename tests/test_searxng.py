from unittest.mock import patch

import httpx
import pytest
from accounts.models import User
from django.core.cache import cache
from enterprises.models import ResearchSettings
from enterprises.research import ResearchUnavailable, search_sources
from enterprises.search import SearchUnavailable, searxng_request, validate_searxng_url
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_admin_can_enable_local_search_without_key_and_customer_sees_ready():
    user = User.objects.create_user(username="search-admin", is_staff=True, is_superuser=True)
    client = APIClient()
    client.force_authenticate(user)
    url = "/api/v1/admin/enterprise-research-settings"
    response = client.patch(url, {"provider": "searxng", "enabled": True}, format="json")
    assert response.status_code == 200
    assert response.data["has_api_key"] is False
    assert client.get("/api/v1/enterprises/options").data["search_ready"] is True
    assert client.patch(url, {"provider": "brave"}, format="json").status_code == 400
    user.is_staff = user.is_superuser = False
    user.save()
    assert client.post(url, {"mode": "connection"}, format="json").status_code == 403
    with patch("enterprises.views.get_ai_profile") as model, patch("enterprises.views.enqueue"):
        model.return_value.configured = True
        assert client.post("/api/v1/enterprise-research", {
            "name": "合成企业", "source_mode": "search",
        }, format="json").status_code == 202


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://metadata.internal", "http://searxng:8080@other/", "http://searxng:8080/?x=1"])
def test_endpoint_rejects_unapproved_internal_or_redirect_targets(url):
    with pytest.raises(SearchUnavailable):
        validate_searxng_url(url)


def test_json_disabled_and_empty_engine_failure_are_distinguishable():
    config = ResearchSettings(provider="searxng", enabled=True)
    with patch("enterprises.search.httpx.Client") as factory:
        factory.return_value.__enter__.return_value.get.return_value = httpx.Response(
            403, request=httpx.Request("GET", "http://searxng:8080/search"))
        with pytest.raises(SearchUnavailable, match="JSON"):
            searxng_request(config, "test", use_cache=False)
    with patch("enterprises.research.ResearchSettings.objects.get_or_create", return_value=(config, False)), patch(
        "enterprises.research.searxng_request", return_value={"results": [], "unresponsive_count": 2}
    ):
        with pytest.raises(ResearchUnavailable, match="不代表企业没有"):
            search_sources({"name": "合成企业"})


@pytest.mark.django_db
def test_search_only_sends_identity_and_marks_snippet_when_page_unreadable():
    ResearchSettings.objects.create(provider="searxng", enabled=True)
    rows = {"results": [{"url": "https://example.com/about", "title": "合成企业", "content": "合成企业主营污水处理。"}], "unresponsive_count": 0}
    with patch("enterprises.research.searxng_request", return_value=rows) as request, patch(
        "enterprises.research.website_sources", side_effect=ValueError("blocked")
    ):
        sources = search_sources({"name": "合成企业", "introduction": "私有技术资料", "description": "保密项目"})
    assert "私有" not in request.call_args.args[1] and "保密" not in request.call_args.args[1]
    assert sources[0]["material"] == "搜索摘要"
    assert "read_warning" in sources[0]


def test_upstream_outage_has_shared_cooldown_even_for_manual_tests():
    cache.clear()
    config = ResearchSettings(provider="searxng", enabled=True)
    response = httpx.Response(200, json={"results": [], "unresponsive_engines": [["brave", "too many requests"]]}, request=httpx.Request("GET", "http://searxng:8080/search"))
    try:
        with patch("enterprises.search.httpx.Client") as factory:
            client = factory.return_value.__enter__.return_value
            client.get.return_value = response
            with pytest.raises(SearchUnavailable, match="不代表企业没有"):
                searxng_request(config, "company-a", use_cache=False)
            with pytest.raises(SearchUnavailable, match="暂停请求一分钟"):
                searxng_request(config, "company-b", use_cache=False)
            assert client.get.call_count == 1
    finally:
        cache.clear()


def test_partial_results_cache_but_empty_failures_do_not():
    cache.clear()
    config = ResearchSettings(provider="searxng", enabled=True)
    response = httpx.Response(200, json={"results": [{"title": "合成结果"}], "unresponsive_engines": [["test", "timeout"]]}, request=httpx.Request("GET", "http://searxng:8080/search"))
    with patch("enterprises.search.httpx.Client") as factory:
        client = factory.return_value.__enter__.return_value
        client.get.return_value = response
        assert searxng_request(config, "synthetic")["unresponsive_count"] == 1
        searxng_request(config, "synthetic")
        assert client.get.call_count == 1
        assert factory.call_args.kwargs["trust_env"] is False
    cache.clear()


@pytest.mark.django_db
def test_scheduled_refresh_accepts_searxng_without_key():
    from datetime import timedelta

    from django.utils import timezone
    from enterprises.models import ResearchRun
    from enterprises.tasks import schedule_refreshes
    from test_enterprise_profiles import profile_for

    user = User.objects.create_user(username="refresh-local-search")
    profile = profile_for(user)
    profile.refresh_days = 7
    profile.next_research_at = timezone.now() - timedelta(minutes=1)
    profile.save()
    ResearchSettings.objects.create(provider="searxng", enabled=True)
    with patch("enterprises.tasks.get_ai_profile") as model, patch("enterprises.tasks.enqueue"):
        model.return_value.configured = True
        schedule_refreshes()
    assert ResearchRun.objects.filter(profile=profile).count() == 1


def test_company_identity_results_take_priority_before_source_budget():
    config = ResearchSettings(provider="searxng", enabled=True, max_sources=2)
    unrelated = [{"url": f"https://example.com/generic-{i}", "title": "中国介绍", "content": "国家概况"}
                 for i in range(6)]
    target = {"url": "https://example.com/company", "title": "合成企业有限公司", "content": "主营污水治理"}
    partial = {"url": "https://example.com/report", "title": "行业报道", "content": "合成企业有限公司参与项目"}
    with patch("enterprises.research.searxng_request", return_value={
        "results": unrelated + [partial, target], "unresponsive_count": 1,
    }):
        sources = search_sources({"name": "合成企业有限公司"}, config=config, read_pages=False)
    assert [source["url"] for source in sources] == [target["url"], partial["url"]]
    assert sources[0]["search_warning"]
    assert all(source["material"] == "搜索摘要" for source in sources)
