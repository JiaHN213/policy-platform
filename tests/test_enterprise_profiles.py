from datetime import timedelta
from unittest.mock import patch

import pytest
from accounts.models import Membership, Organization, User
from django.utils import timezone
from enterprises.models import EnterpriseProfile, EnterpriseProject, ResearchRun, ResearchSettings
from enterprises.research import CompanyDraft, validate_draft
from enterprises.tasks import process_research, schedule_refreshes
from rest_framework.test import APIClient


@pytest.fixture
def customer():
    user = User.objects.create_user(username="enterprise-customer", password="test-only-pass")
    client = APIClient()
    client.force_authenticate(user)
    return user, client


def profile_for(user, name="测试水务有限公司", **data):
    organization = Organization.objects.create(name=name)
    Membership.objects.create(user=user, organization=organization, role="admin")
    return EnterpriseProfile.objects.create(organization=organization, data=data, confirmed_at=timezone.now())


def policy(**kwargs):
    from policies.models import Policy
    defaults = dict(title="智慧水务设备更新支持政策", issuer="测试部门", publication_date=timezone.now().date(),
                    body="支持污水处理设施进行智慧水务改造。", summary="支持智慧水务改造。", source_url="https://example.gov.cn/policy",
                    source_key="enterprise-test", content_hash="hash", status="published", source_grade="L1",
                    validity_status="effective", geographic_level="national", business_domains=["urban_sewage"], direction_tags=["smart_water"])
    return Policy.objects.create(**(defaults | kwargs))


@pytest.mark.django_db
def test_same_name_does_not_join_another_tenant(customer):
    user, client = customer
    other = User.objects.create_user(username="other-enterprise")
    private = profile_for(other, business_summary="私有资料")
    response = client.post("/api/v1/enterprises", {"name": private.organization.name, "data": {"business_domains": ["urban_sewage"]}}, format="json")
    assert response.status_code == 201
    created = EnterpriseProfile.objects.get(pk=response.data["id"])
    assert created.organization_id != private.organization_id
    assert client.get(f"/api/v1/enterprises/{private.pk}").status_code == 404
    assert client.get("/api/v1/enterprises").data["items"][0]["id"] == str(created.pk)


@pytest.mark.django_db
def test_stale_profile_update_and_evidence_isolation(customer):
    user, client = customer
    profile = profile_for(user, city="南宁")
    profile.evidence = {"city": {"origin": "公开资料，经用户确认", "sources": [{"quote": "位于南宁"}]}}
    profile.save()
    url = f"/api/v1/enterprises/{profile.pk}"
    body = {"name": profile.organization.name, "revision": 1, "data": {"city": "柳州"}}
    result = client.patch(url, body, format="json")
    assert result.status_code == 200
    assert result.data["evidence"]["city"]["sources"] == []
    assert client.patch(url, body, format="json").status_code == 409
    profile.refresh_from_db()
    assert profile.revision == 2 and profile.data["city"] == "柳州"


@pytest.mark.django_db
def test_member_can_read_but_cannot_modify(customer):
    user, client = customer
    profile = profile_for(user)
    Membership.objects.filter(user=user).update(role="member")
    assert client.get(f"/api/v1/enterprises/{profile.pk}").status_code == 200
    result = client.patch(f"/api/v1/enterprises/{profile.pk}", {"name": profile.organization.name, "revision": 1, "data": {}}, format="json")
    assert result.status_code == 403


@pytest.mark.django_db
def test_missing_search_key_does_not_queue_fake_research(customer):
    user, client = customer
    with patch("enterprises.views.get_ai_profile") as ai:
        ai.return_value.configured = True
        result = client.post("/api/v1/enterprise-research", {"name": "测试水务有限公司"}, format="json")
    assert result.status_code == 400
    assert "尚未启用" in result.data["message"]
    assert not ResearchRun.objects.exists()


def test_invalid_identity_and_quotes_are_not_saved():
    sources = [{"id": 1, "url": "https://example.com/about", "title": "简介", "text": "测试水务有限公司主营污水处理。", "retrieved_at": "2026-09-29", "material": "页面正文", "source_type": "公开网页"}]
    draft = CompanyDraft.model_validate({"candidates": [
        {"name": "另一家公司", "identity_evidence": {"source_id": 1, "quote": "测试水务有限公司"}, "fields": []},
        {"name": "测试水务有限公司", "identity_evidence": {"source_id": 1, "quote": "测试水务有限公司"}, "fields": [
            {"field": "city", "value": "南宁", "evidence": [{"source_id": 1, "quote": "企业位于南宁市"}]},
            {"field": "business_domains", "value": ["urban_sewage", "made_up_tag"], "evidence": [{"source_id": 1, "quote": "主营污水处理"}]},
        ]},
    ]})
    result = validate_draft(draft, sources, {})
    assert len(result["candidates"]) == 1
    assert "city" not in result["candidates"][0]["data"]
    assert result["candidates"][0]["data"]["business_domains"] == ["urban_sewage"]


def test_docker_ollama_uses_native_structured_output():
    from core.ai_runtime import AIProfile
    native = AIProfile("enterprise", "http://host.docker.internal:11434/v1", "", "qwen3.5:4b", True)
    assert native.is_local and native.configured
    other = AIProfile("enterprise", "http://host.docker.internal:8001/v1", "key", "model", True)
    assert not other.is_local


@pytest.mark.django_db
def test_completed_research_never_overwrites_confirmed_profile(customer):
    user, _ = customer
    profile = profile_for(user, city="用户确认的城市")
    run = ResearchRun.objects.create(user=user, profile=profile, inputs={"name": profile.organization.name}, fingerprint="x")
    with patch("enterprises.tasks.company_graph", return_value={"candidates": [{"data": {"city": "新城市"}}]}):
        process_research(str(run.pk))
        process_research(str(run.pk))
    profile.refresh_from_db()
    run.refresh_from_db()
    assert run.status == "completed"
    assert profile.data["city"] == "用户确认的城市"
    assert profile.revision == 1


@pytest.mark.django_db
def test_matches_use_confirmed_tags_and_only_visible_policies(customer):
    user, client = customer
    profile = profile_for(user, business_domains=["urban_sewage"], direction_tags=["smart_water"])
    published = policy()
    policy(source_key="private", status="candidate")
    policy(source_key="l4", source_grade="L4")
    policy(source_key="expired", validity_status="expired")
    response = client.get(f"/api/v1/enterprises/{profile.pk}/matches")
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["items"][0]["policy_id"] == str(published.pk)
    assert response.data["items"][0]["level"] == "high"
    profile.data = {}
    profile.save()
    assert client.get(f"/api/v1/enterprises/{profile.pk}/matches").data["items"][0]["level"] == "insufficient"


@pytest.mark.django_db
def test_opportunity_deadline_is_checked_at_request_time(customer):
    from policies.models import Opportunity, OpportunityBatch
    user, client = customer
    profile = profile_for(user, business_domains=["urban_sewage"])
    document = policy()
    evidence = dict(evidence_policy=document, evidence_version=1, evidence_quote="支持污水处理设施", verification_status="verified")
    opportunity = Opportunity.objects.create(policy=document, title="设备更新", category="fiscal", status="open", **evidence)
    OpportunityBatch.objects.create(opportunity=opportunity, name="本批", status="open", deadline_at=timezone.now() - timedelta(hours=1), **evidence)
    result = client.get(f"/api/v1/enterprises/{profile.pk}/matches?view=opportunities")
    assert result.data["count"] == 0
    assert result.data["excluded_closed_opportunities"] == 1


@pytest.mark.django_db
def test_project_and_run_cannot_cross_tenants(customer):
    _, client = customer
    other = User.objects.create_user(username="another-customer")
    profile = profile_for(other)
    project = EnterpriseProject.objects.create(profile=profile, name="保密项目")
    run = ResearchRun.objects.create(user=other, profile=profile, inputs={"name": "私有企业"}, fingerprint="private")
    assert client.get(f"/api/v1/enterprise-projects/{project.pk}").status_code == 404
    assert client.get(f"/api/v1/enterprise-research/{run.pk}").status_code == 404
    assert client.post("/api/v1/enterprise-projects", {"profile": str(profile.pk), "name": "越权项目"}, format="json").status_code == 403


@pytest.mark.django_db
def test_search_configuration_key_is_write_only(customer):
    user, client = customer
    assert client.get("/api/v1/admin/enterprise-research-settings").status_code == 403
    user.is_staff = True
    user.is_superuser = True
    user.save()
    result = client.patch("/api/v1/admin/enterprise-research-settings", {"api_key": "test-key-never-echo", "enabled": True}, format="json")
    assert result.status_code == 200
    assert result.data["has_api_key"] is True
    assert "test-key-never-echo" not in str(result.data)
    assert ResearchSettings.objects.get().api_key == "test-key-never-echo"
    result = client.patch("/api/v1/admin/enterprise-research-settings", {"clear_api_key": True}, format="json")
    assert result.data["has_api_key"] is False and result.data["enabled"] is False


@pytest.mark.django_db
def test_scheduled_refresh_creates_a_draft_without_changing_profile(customer):
    user, _ = customer
    profile = profile_for(user, city="南宁")
    profile.refresh_days = 7
    profile.next_research_at = timezone.now() - timedelta(minutes=1)
    profile.save()
    ResearchSettings.objects.create(api_key="test-key", enabled=True)
    with patch("enterprises.tasks.get_ai_profile") as ai, patch("enterprises.tasks.enqueue") as enqueue:
        ai.return_value.configured = True
        schedule_refreshes()
        schedule_refreshes()
    assert enqueue.call_count == 1
    profile.refresh_from_db()
    assert profile.data == {"city": "南宁"} and profile.revision == 1
    assert profile.next_research_at > timezone.now()
    assert ResearchRun.objects.get().profile_id == profile.pk


@pytest.mark.django_db
def test_cached_request_and_quota_enforced(customer):
    user, client = customer
    ResearchSettings.objects.create(api_key="test-key", enabled=True, daily_limit=1)
    with patch("enterprises.views.get_ai_profile") as ai, patch("enterprises.views.enqueue"):
        ai.return_value.configured = True
        first = client.post("/api/v1/enterprise-research", {"name": "测试水务公司"}, format="json")
        second = client.post("/api/v1/enterprise-research", {"name": "测试水务公司"}, format="json")
        blocked = client.post("/api/v1/enterprise-research", {"name": "其他水务公司"}, format="json")
    assert first.status_code == 202 and second.data["id"] == first.data["id"]
    assert blocked.status_code == 400 and "次数已用完" in blocked.data["message"]


@pytest.mark.django_db
def test_explanations_hidden_after_withdrawal_or_membership_revocation(customer):
    user, client = customer
    profile = profile_for(user)
    document = policy()
    run = ResearchRun.objects.create(user=user, profile=profile, kind="explanation", fingerprint="explain", status="completed",
                                     inputs={"policy_id": str(document.pk), "policy_version": 1, "profile_revision": 1}, result={"points": ["private"]})
    document.status = "withdrawn"
    document.save()
    assert client.get(f"/api/v1/enterprise-research/{run.pk}").status_code == 409
    assert client.get("/api/v1/enterprise-research").data["items"] == []
    run.kind = "company"
    run.save()
    Membership.objects.filter(user=user).update(active=False)
    assert client.get(f"/api/v1/enterprise-research/{run.pk}").status_code == 404
    assert client.get("/api/v1/enterprise-research").data["items"] == []
