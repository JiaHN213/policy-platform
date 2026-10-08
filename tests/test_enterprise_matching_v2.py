from datetime import date, timedelta

import pytest
from accounts.models import Membership, Organization, User
from django.utils import timezone
from enterprises.conditions import assess_conditions, compare_condition
from enterprises.match_analysis import Analysis, explain_match
from enterprises.matching import match_policies
from enterprises.models import EnterpriseProfile, EnterpriseProject, ResearchRun
from enterprises.policy_evidence import policy_signature, select_passages
from enterprises.tasks import process_research
from policies.models import Opportunity, OpportunityBatch, Policy
from policies.opensearch import OpenSearchUnavailable
from policies.readiness import evidence_readiness
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def data(monkeypatch):
    user = User.objects.create_user("match-v2")
    org = Organization.objects.create(name="合成水务企业")
    Membership.objects.create(user=user, organization=org, role="admin")
    profile = EnterpriseProfile.objects.create(organization=org, data={"business_domains": ["urban_sewage"]})
    policy = Policy.objects.create(source_key="match-v2", title="设备支持", body="支持城镇污水处理。企业注册地必须为南宁市。", status="published", source_grade="L1", validity_status="effective", publication_date=date(2026, 1, 1), source_url="https://example.gov.cn/policy", city="南宁市")
    opportunity = Opportunity.objects.create(policy=policy, title="设备补助", category="fiscal", status="open", requirements=["企业注册地必须为南宁市。"], evidence_policy=policy, evidence_version=policy.version, evidence_quote=policy.body, verification_status="verified")
    client = APIClient()
    client.force_authenticate(user)
    monkeypatch.setattr("enterprises.retrieval.configured", lambda: False)
    return user, profile, policy, opportunity, client


@pytest.mark.parametrize("clause,facts,project,expected", [
    ("项目总投资不低于100万元。", {}, {"investment_wan": "100"}, "consistent"),
    ("项目总投资不低于100万元。", {}, {"investment_wan": "99.9"}, "conflict"),
    ("项目总投资不低于100万元。", {}, {}, "unknown"),
    ("补助金额不超过100万元。", {}, {"investment_wan": "200"}, "unknown"),
    ("2025年营业收入不低于100万元。", {"annual_revenue_year": "2024", "annual_revenue_wan": "50"}, {}, "unknown"),
    ("2025年营业收入不低于100万元。", {"annual_revenue_year": "2025", "annual_revenue_wan": "50"}, {}, "conflict"),
    ("申报主体须为事业单位。", {"subject_type": "企业"}, {}, "conflict"),
    ("项目阶段须为在建。", {}, {"project_stage": "拟建"}, "conflict"),
    ("企业不得具有重复补助。", {}, {}, "unknown"),
    ("企业不得具有重复补助。", {"capabilities": ["重复补助"]}, {}, "conflict"),
    ("企业注册地必须为南宁市或柳州市。", {"registered_city": "桂林市"}, {}, "unknown"),
])
def test_typed_conditions_do_not_treat_unknown_as_conflict(clause, facts, project, expected):
    assert compare_condition(clause, facts, project)[0] == expected


def test_recall_without_tags_keeps_unknown_but_filters_explicit_conflict(data):
    user, profile, policy, _, client = data
    results = match_policies(user, profile, view="opportunities")
    assert results["items"][0]["level"] == "medium"
    assert results["items"][0]["conditions"]["status"] == "unknown"
    assert results["items"][0]["supplement_fields"] == ["registered_city"]
    profile.data["registered_city"] = "柳州市"
    profile.save()
    result = client.get(f"/api/v1/enterprises/{profile.pk}/matches?view=opportunities")
    assert result.data["count"] == 0 and result.data["conflicting_policies"] == 1
    result = client.get(f"/api/v1/enterprises/{profile.pk}/matches?view=opportunities&include_conflicts=true")
    assert result.data["items"][0]["policy_id"] == str(policy.pk)


def test_manual_filters_not_relaxed_and_project_uses_own_tags(data):
    user, profile, policy, _, client = data
    root = f"/api/v1/enterprises/{profile.pk}/matches?view=opportunities"
    for query in ["city=柳州市", "category=tax", "q=完全不存在", "source_grade=L2"]:
        assert client.get(root + "&" + query).data["count"] == 0
    project = EnterpriseProject.objects.create(profile=profile, name="其他行业", data={"business_domains": ["water_supply"]})
    item = match_policies(user, profile, project, view="opportunities")["items"][0]
    assert item["level"] == "insufficient" and not item["retrieval"]["terms"]
    assert client.get(root + "&sort=invalid").status_code == 400


def test_index_failure_falls_back_without_hiding_policies(data, monkeypatch):
    user, profile, policy, _, _ = data
    monkeypatch.setattr("enterprises.retrieval.configured", lambda: True)
    def failed(*args, **kwargs):
        raise OpenSearchUnavailable("test")
    monkeypatch.setattr("enterprises.retrieval.search_policy_ids", failed)
    result = match_policies(user, profile)
    assert result["search_backend"] == "database_fallback"
    assert result["items"][0]["policy_id"] == str(policy.pk)


def test_exception_context_cannot_become_hard_filter(data):
    user, profile, policy, opportunity, _ = data
    policy.body = "鼓励企业注册地必须为南宁市。"
    profile.data["registered_city"] = "柳州市"
    condition = assess_conditions(policy, [opportunity], profile.data, None, evidence_readiness(policy))
    assert condition["status"] == "unknown"


def test_late_attachment_is_read_and_invented_sources_are_discarded(data, monkeypatch):
    user, profile, policy, opportunity, _ = data
    policy.body = "前言。" * 9000 + "\n附件：申报指南\n企业注册地必须为南宁市。支持城镇污水处理。"
    profile.data["registered_city"] = "南宁市"
    passages, coverage = select_passages(policy, ["城镇污水处理"], opportunity.requirements)
    assert coverage["partial"] and coverage["selected_characters"] <= 18000
    assert any("企业注册地必须为南宁市" in part["text"] for part in passages)
    def model(_, inputs, schema, **kwargs):
        source = next(p for p in inputs["passages"] if "支持城镇污水处理。" in p["text"])
        return Analysis.model_validate({"points": [
            {"text": "涉及企业已确认业务方向", "source_id": source["source_id"], "quote": "支持城镇污水处理。", "profile_fields": ["enterprise.business_domains"]},
            {"text": "虚构说明", "source_id": "fake", "quote": "企业注册地必须为南宁市。", "profile_fields": ["enterprise.business_domains"]},
        ]})
    monkeypatch.setattr("enterprises.match_analysis.model_json", model)
    result = explain_match(user, profile, None, policy)
    assert len(result["points"]) == 1
    assert result["points"][0]["source"]["source_type"] == "attachment"
    assert result["conditions"]["opportunities"][0]["checks"][0]["source"]["source_type"] == "attachment"


def test_batch_change_invalidates_cached_analysis_and_late_result(data, monkeypatch):
    user, profile, policy, opportunity, client = data
    batch = OpportunityBatch.objects.create(opportunity=opportunity, name="本批次", status="open", deadline_at=timezone.now() + timedelta(days=2), evidence_policy=policy, evidence_version=1, evidence_quote=policy.body, verification_status="verified")
    inputs = {"policy_id": str(policy.pk), "policy_version": policy.version, "profile_revision": profile.revision, "project_revision": None, "matching_signature": policy_signature(user, policy)}
    run = ResearchRun.objects.create(user=user, profile=profile, kind="explanation", fingerprint="v2", inputs=inputs)
    def model(*args, **kwargs):
        batch.deadline_at = timezone.now() - timedelta(minutes=1)
        batch.save()
        return Analysis(points=[])
    monkeypatch.setattr("enterprises.match_analysis.model_json", model)
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "failed" and not run.result
    assert client.get(f"/api/v1/enterprise-research/{run.pk}").status_code == 409
    assert match_policies(user, profile, view="opportunities")["count"] == 0
    policy.status = "withdrawn"
    policy.save()
    assert match_policies(user, profile)["count"] == 0


def test_closed_opportunity_cannot_start_new_analysis(data):
    _, profile, policy, opportunity, client = data
    opportunity.status = "closed"
    opportunity.save()
    response = client.post("/api/v1/enterprise-research", {"kind": "explanation", "profile_id": str(profile.pk), "policy_id": str(policy.pk), "matching_view": "opportunities"}, format="json")
    assert response.status_code == 409
    assert not ResearchRun.objects.exists()


def test_new_model_condition_is_not_an_automatic_exclusion(data, monkeypatch):
    user, profile, policy, _, _ = data
    policy.body += "项目总投资不低于100万元。"
    project = EnterpriseProject.objects.create(profile=profile, name="合成项目", data={"investment_wan": "50"})
    def model(_, inputs, schema, **kwargs):
        return Analysis.model_validate({"conditions": [{"kind": "amount", "source_id": inputs["passages"][0]["source_id"], "quote": "项目总投资不低于100万元。"}]})
    monkeypatch.setattr("enterprises.match_analysis.model_json", model)
    result = explain_match(user, profile, project, policy)
    check = result["additional_conditions"][0]
    assert check["status"] == "unknown" and check["tentative_status"] == "conflict"


def test_project_supplement_preserves_other_fields_and_rejects_bad_amount(data):
    _, profile, _, _, client = data
    project = EnterpriseProject.objects.create(profile=profile, name="合成项目", data={"city": "南宁市", "business_domains": ["urban_sewage"]})
    url = f"/api/v1/enterprise-projects/{project.pk}"
    payload = {"name": project.name, "profile": str(profile.pk), "revision": project.revision,
               "data": {**project.data, "investment_wan": "100.5", "project_stage": "在建"}}
    result = client.patch(url, payload, format="json")
    assert result.status_code == 200, result.data
    assert result.data["data"]["city"] == "南宁市"
    payload["revision"] = result.data["revision"]
    payload["data"]["investment_wan"] = "一百万元"
    assert client.patch(url, payload, format="json").status_code == 400
    assert client.patch(f"/api/v1/enterprises/{profile.pk}", {"name": profile.organization.name, "revision": profile.revision, "data": {"investment_wan": "100"}}, format="json").status_code == 400
