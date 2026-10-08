from dataclasses import replace
from datetime import date
from unittest.mock import Mock, patch

import pytest
from accounts.models import User
from core.ai_runtime import AIProfile, apply_generation_settings, ensure_ai_profiles, get_ai_profile
from core.config_views import AIModelProfileSerializer
from core.models import AIModelProfile
from django.core import signing
from django.core.cache import caches
from policies.models import Policy
from policies.search_support import SALT, excerpts
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def search_case(settings):
    settings.AI_BASE_URL, settings.AI_MODEL, settings.AI_API_KEY = "http://localhost:11434/v1", "local-test", ""
    user = User.objects.create_user(username="search-owner")
    client = APIClient()
    client.force_authenticate(user)
    policy = Policy.objects.create(title="供水政策", issuer="水利局", publication_date=date(2026, 1, 1),
        body="支持供水设施改造。申报须符合原文条件。", source_url="https://example.gov.cn/a", source_key="a", content_hash="a", source_grade="L1", status="published")
    return user, client, policy


def deferred(client):
    response = client.post("/api/v1/search", {"q": "供水政策", "mode": "natural", "defer_summary": True}, format="json")
    assert response.status_code == 200, response.data
    return response.data["summary_token"]


def test_split_inherits_existing_service_but_can_be_configured_independently():
    AIModelProfile.objects.create(purpose="search", model="old-search", base_url="http://localhost:11434/v1", thinking=True, context_tokens=32768)
    AIModelProfile.objects.create(purpose="enterprise", model="old-enterprise", base_url="http://localhost:11434/v1", max_output_tokens=2048)
    ensure_ai_profiles()
    assert AIModelProfile.objects.count() == 7
    assert get_ai_profile("search_summary").thinking
    assert get_ai_profile("search_summary").context_tokens == 32768
    assert get_ai_profile("enterprise_match").model == "old-enterprise"
    AIModelProfile.objects.filter(purpose="search_summary").update(model="new-summary")
    ensure_ai_profiles()
    assert get_ai_profile("search").model == "old-search"
    assert get_ai_profile("search_summary").model == "new-summary"


def test_model_parameters_reach_supported_transport_and_keep_task_limit():
    profile = AIProfile("search", "http://localhost:11434/v1", "", "local", True, thinking=True, context_tokens=32768, max_output_tokens=1024)
    payload = apply_generation_settings(profile, {"options": {"num_predict": 600}})
    assert payload == {"think": True, "options": {"num_ctx": 32768, "num_predict": 600}}
    remote = replace(profile, base_url="https://example.com/v1", api_key="test-only")
    assert apply_generation_settings(remote, {"max_tokens": 2048}) == {"max_tokens": 1024}
    serializer = AIModelProfileSerializer(data={"base_url": remote.base_url, "model": "x", "context_tokens": 2048, "max_output_tokens": 2048})
    assert not serializer.is_valid()


def test_list_is_returned_without_generation_and_reuses_intent(search_case):
    _, client, _ = search_case
    with patch("policies.search.parse_intent", return_value={"keywords": ["供水"]}) as intent, patch("policies.search.generate") as generate:
        deferred(client)
        deferred(client)
        assert intent.call_count == 1
        generate.assert_not_called()


def test_summary_is_scoped_cached_and_invalidated_by_policy_change(search_case):
    user, client, policy = search_case
    with patch("policies.search.parse_intent", return_value={"keywords": ["供水"]}):
        token = deferred(client)
    outsider = APIClient()
    outsider.force_authenticate(User.objects.create_user(username="other"))
    assert outsider.post("/api/v1/search/summary", {"token": token}, format="json").status_code == 403
    with patch("policies.search.generate", return_value={"claims": [{"evidence_id": str(policy.pk), "quote": "支持供水设施改造。"}]}) as generate:
        for _ in range(2):
            response = client.post("/api/v1/search/summary", {"token": token}, format="json")
            assert response.status_code == 200 and response.data["claims"]
        assert generate.call_count == 1
        policy.status = "withdrawn"
        policy.save()
        assert client.post("/api/v1/search/summary", {"token": token}, format="json").status_code == 409
        assert generate.call_count == 1
    assert client.post("/api/v1/search/summary", {"token": token + "tamper"}, format="json").status_code == 400
    with patch("django.core.signing.time.time", return_value=0):
        expired = signing.dumps({"user": user.pk}, salt=SALT)
    assert client.post("/api/v1/search/summary", {"token": expired}, format="json").status_code == 400


def test_summary_failure_does_not_erase_list_and_refreshes_permissions(search_case):
    user, client, _ = search_case
    with patch("policies.search.parse_intent", return_value={"keywords": ["供水"]}):
        token = deferred(client)
    with patch("policies.search.generate", side_effect=RuntimeError("network")):
        response = client.post("/api/v1/search/summary", {"token": token}, format="json")
        assert response.status_code == 200 and response.data["claims"] == []
    with patch("policies.search.generate") as generate:
        def revoke(*args):
            User.objects.filter(pk=user.pk).update(is_active=False)
            return {"claims": []}
        generate.side_effect = revoke
        assert client.post("/api/v1/search/summary", {"token": token}, format="json").status_code == 403


def test_short_excerpts_find_late_relevant_passages_and_remain_literal():
    body = "普通背景介绍。\n" * 900 + "支持供水设施设备更新，申请人须提供证明。\n" + "其他内容。\n" * 50
    result = excerpts(body, ["供水", "设备更新"])
    assert "支持供水设施设备更新" in result and len(result) <= 1200
    assert all(part in body for part in result.splitlines())


def test_cache_failure_falls_back_and_model_change_invalidates_intent(search_case):
    _, client, _ = search_case
    intent = Mock(return_value={"keywords": ["供水"]})
    with patch("policies.search.parse_intent", intent):
        deferred(client)
        AIModelProfile.objects.create(purpose="search", base_url="http://localhost:11434/v1", model="different")
        deferred(client)
        assert intent.call_count == 2
        with patch.object(caches["search"], "get", side_effect=ConnectionError), patch.object(caches["search"], "set", side_effect=ConnectionError):
            deferred(client)
        assert intent.call_count == 3


def test_summary_uses_its_own_model_and_parameters(search_case):
    from analysis.gateway import generate
    AIModelProfile.objects.create(purpose="search_summary", base_url="http://localhost:11434/v1", model="summary-only", thinking=True, context_tokens=32768, max_output_tokens=512)
    with patch("analysis.gateway.request_json", return_value={"message": {"content": '{"claims": [], "gaps": []}'}}) as call:
        generate("供水", {"a": "支持供水。"})
    assert call.call_args.args[1].purpose == "search_summary"
    payload = call.call_args.kwargs["payload"]
    assert payload["model"] == "summary-only" and payload["think"]
    assert payload["options"]["num_ctx"] == 32768 and payload["options"]["num_predict"] == 512


def test_matching_uses_separate_model_purpose(search_case):
    from accounts.models import Membership, Organization
    from enterprises.match_analysis import Analysis, explain_match
    from enterprises.models import EnterpriseProfile
    user, _, policy = search_case
    org = Organization.objects.create(name="供水企业")
    Membership.objects.create(user=user, organization=org, role="admin")
    profile = EnterpriseProfile.objects.create(organization=org, data={"business_summary": "供水服务"})
    with patch("enterprises.match_analysis.model_json", return_value=Analysis()) as model:
        explain_match(user, profile, None, policy)
    assert model.call_args.kwargs["purpose"] == "enterprise_match"
