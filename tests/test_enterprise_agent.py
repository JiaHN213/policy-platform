import uuid

import httpx
import pytest
from accounts.models import Membership, Organization, User
from core.ai_runtime import AIProfile
from enterprises.agent import Decision, snapshot_for
from enterprises.grounding import supported_value
from enterprises.materials import MaterialError, material_source
from enterprises.models import EnterpriseProfile, ResearchArtifact, ResearchRun, ResearchSettings
from enterprises.research import CompanyDraft
from enterprises.tasks import process_research
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db
NAME = "南宁市测试水务有限公司"
TEXT = NAME + "，主营业务为城镇污水处理，位于南宁市。"


@pytest.fixture
def context(monkeypatch):
    user = User.objects.create_user("agent-customer", is_staff=True, is_superuser=True)
    org = Organization.objects.create(name=NAME)
    Membership.objects.create(user=user, organization=org, role="admin")
    profile = EnterpriseProfile.objects.create(organization=org, data={"business_summary": "保留人工信息"})
    config = ResearchSettings.objects.create(key="default", agent_enabled=True, agent_organizations=[str(org.pk)])
    monkeypatch.setattr("enterprises.agent.get_ai_profile", lambda *a: AIProfile("enterprise", "http://ollama:11434/v1", "", "test", True))
    monkeypatch.setattr("enterprises.views.enqueue", lambda run: None)
    client = APIClient()
    client.force_authenticate(user)
    return user, profile, config, client


def make_run(context, mode="text", **inputs):
    user, profile, _, _ = context
    return ResearchRun.objects.create(user=user, profile=profile, fingerprint=uuid.uuid4().hex,
        inputs={"name": NAME, "source_mode": mode, "introduction": TEXT, **inputs},
        uploaded_material=TEXT.encode() if mode == "file" else None, agent_snapshot=snapshot_for(profile))


def extract(instruction, data, schema, **kwargs):
    if schema is Decision:
        return Decision(action="read", option_id=data["options"][0]["id"], reason="补查缺失字段")
    source = next(s for s in data["sources"] if "主营业务" in s["text"])
    return CompanyDraft.model_validate({"candidates": [{"name": NAME, "identity_evidence": {"source_id": source["id"], "quote": NAME}, "fields": [{"field": "business_summary", "value": "主营业务为城镇污水处理", "evidence": [{"source_id": source["id"], "quote": "主营业务为城镇污水处理"}]}]}]})


@pytest.mark.parametrize("mode", ["text", "file", "website", "search"])
def test_four_sources_produce_only_draft_and_traced_evidence(context, monkeypatch, mode):
    monkeypatch.setattr("enterprises.agent.model_json", extract)
    monkeypatch.setattr("enterprises.agent.website_sources", lambda *a, **k: ([material_source(TEXT, "官网", url="https://example.com", material="网页正文")], []))
    monkeypatch.setattr("enterprises.agent.search_sources", lambda *a, **k: [material_source(TEXT, "公开资料", url="https://example.com")])
    run = make_run(context, mode, file_name="介绍.txt", website="https://example.com")
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "completed", run.error
    assert run.result["candidates"][0]["data"]["business_summary"]
    assert run.result["candidates"][0]["evidence"]["business_summary"][0]["start_offset"] >= 0
    assert ResearchArtifact.objects.filter(run=run).exists()
    context[1].refresh_from_db()
    assert context[1].data == {"business_summary": "保留人工信息"}
    assert run.steps.filter(status="completed").exists()
    assert len(run.result["warnings"]) == len(set(run.result["warnings"]))
    if mode == "website":
        assert any("企业提供的官网：https://example.com" in s["text"] for s in run.result["sources"])
        assert run.result["candidates"][0]["data"]["website"] == "https://example.com"


def test_long_material_reads_later_segment_without_search(context, monkeypatch):
    calls = []
    def model(*args, **kwargs):
        calls.append(args[2])
        return extract(*args, **kwargs)
    monkeypatch.setattr("enterprises.agent.model_json", model)
    monkeypatch.setattr("enterprises.agent.search_sources", lambda *a, **k: pytest.fail("私有材料不应联网搜索"))
    run = make_run(context, introduction=TEXT + "介绍。" * 1500 + TEXT)
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "completed"
    assert any(s.get("start_offset", 0) > 0 for s in run.result["sources"])
    assert Decision in calls


@pytest.mark.parametrize("action", ["read", "finish"])
@pytest.mark.parametrize("short_name", [False, True])
def test_search_reads_discovered_page_when_decision_omits_id(context, monkeypatch, action, short_name):
    pages_read = []
    snippet = material_source(NAME + " 企业信息查询，更多内容请查看网页。", NAME,
                              url="https://example.com", material="搜索摘要")
    monkeypatch.setattr("enterprises.agent.search_sources", lambda *a, **k: [snippet])

    def website(url, progress, **kwargs):
        pages_read.append(url)
        return [material_source(TEXT, NAME, url=url, material="网页正文")], []

    def model(instruction, data, schema, **kwargs):
        if schema is Decision:
            return Decision(action="finish" if pages_read else action,
                            reason="需要读取选项1获取企业业务信息。")
        if not pages_read:
            return CompanyDraft(candidates=[])
        return extract(instruction, data, schema, **kwargs)

    monkeypatch.setattr("enterprises.agent.website_sources", website)
    monkeypatch.setattr("enterprises.agent.model_json", model)
    run = make_run(context, "search", name=NAME.removesuffix("有限公司") if short_name else NAME)
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "completed", run.error
    assert pages_read == ["https://example.com"]
    assert run.result["candidates"][0]["name"] == NAME
    assert run.result["candidates"][0]["data"]["business_summary"]
    assert run.usage["reads"] == 2
    assert any(s["material"] == "网页正文" for s in run.result["sources"])


def test_search_name_candidate_does_not_accept_subsidiary(context, monkeypatch):
    other = NAME.removesuffix("有限公司") + "设备有限公司"
    source = material_source(other + "，主营业务为城镇污水处理。", other,
                             url="https://example.com", material="网页正文")
    monkeypatch.setattr("enterprises.agent.search_sources", lambda *a, **k: [source])

    def model(instruction, data, schema, **kwargs):
        if schema is Decision:
            return Decision(action="finish", reason="没有本企业资料")
        return CompanyDraft.model_validate({"candidates": [{"name": other,
            "identity_evidence": {"source_id": 1, "quote": other}, "fields": []}]})

    monkeypatch.setattr("enterprises.agent.model_json", model)
    run = make_run(context, "search", name=NAME.removesuffix("有限公司"))
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "completed" and run.result["candidates"] == []


def test_search_fallback_respects_read_budget(context, monkeypatch):
    context[2].agent_max_reads = 1
    context[2].save()
    monkeypatch.setattr("enterprises.agent.search_sources", lambda *a, **k: [
        material_source(NAME + " 企业信息", NAME, url="https://example.com", material="搜索摘要")])
    monkeypatch.setattr("enterprises.agent.website_sources", lambda *a, **k: pytest.fail("不能突破读取预算"))
    monkeypatch.setattr("enterprises.agent.model_json", lambda instruction, data, schema, **kw:
        Decision(action="read", reason="读取网页") if schema is Decision else CompanyDraft(candidates=[]))
    run = make_run(context, "search")
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "completed" and run.usage["reads"] == 1
    assert run.result["budget_exhausted"] is True


@pytest.mark.parametrize("quote,expected", [
    ("2023年，公司成立了华南分公司和信息技术分公司。", ""),
    ("2023年公司成立。2025年营业收入500万元。", ""),
    ("2023年营业收入500万元，较2022年增长。", ""),
    ("公司预计2023年营业收入500万元。", ""),
    ("公司2023年度营业收入500万元。", "2023"),
])
def test_revenue_year_requires_reporting_evidence(quote, expected):
    assert supported_value("annual_revenue_year", "2023", [{"quote": quote}]) == expected


@pytest.mark.parametrize("error", [ValueError("MODEL_INVALID_OUTPUT"), MaterialError("官网暂时无法连接或证书验证失败，请检查网址，或改为上传、粘贴介绍。"), httpx.ReadTimeout("private internal message")])
def test_optional_enrichment_failure_keeps_grounded_draft(context, monkeypatch, error):
    count = 0
    def model(instruction, data, schema, **kwargs):
        nonlocal count
        if schema is Decision:
            return Decision(action="read", option_id=data["options"][0]["id"], reason="补充可选信息")
        count += 1
        if count > 1:
            raise error
        result = extract(instruction, data, schema, **kwargs)
        field_type = type(result.candidates[0].fields[0])
        result.candidates[0].fields.append(field_type.model_validate({"field": "business_domains", "value": ["urban_sewage"], "evidence": [{"source_id": data["sources"][0]["id"], "quote": "主营业务为城镇污水处理"}]}))
        return result
    monkeypatch.setattr("enterprises.agent.model_json", model)
    run = make_run(context, introduction=TEXT + "介绍。" * 1500 + TEXT)
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "completed", run.error
    assert run.result["partial"] is True
    assert run.result["candidates"][0]["data"]["business_domains"] == ["urban_sewage"]
    assert any("已保留" in warning for warning in run.result["warnings"])
    assert "private internal message" not in str(run.result)
    context[1].refresh_from_db()
    assert context[1].data == {"business_summary": "保留人工信息"}


def test_initial_model_output_failure_does_not_create_completed_draft(context, monkeypatch):
    def model(*args, **kwargs):
        raise ValueError("MODEL_INVALID_OUTPUT")
    monkeypatch.setattr("enterprises.agent.model_json", model)
    run = make_run(context)
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "failed" and not run.result


def test_later_model_omission_does_not_erase_verified_domain(context, monkeypatch):
    count = 0
    def model(instruction, data, schema, **kwargs):
        nonlocal count
        if schema is Decision:
            return Decision(action="read", option_id=data["options"][0]["id"], reason="补充可选信息") if count == 1 else Decision(action="finish", reason="结束补查")
        count += 1
        result = extract(instruction, data, schema, **kwargs)
        if count == 1:
            field_type = type(result.candidates[0].fields[0])
            result.candidates[0].fields.append(field_type.model_validate({"field": "business_domains", "value": ["urban_sewage"], "evidence": [{"source_id": data["sources"][0]["id"], "quote": "主营业务为城镇污水处理"}]}))
        return result
    monkeypatch.setattr("enterprises.agent.model_json", model)
    run = make_run(context, introduction=TEXT + "介绍。" * 1500 + TEXT)
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "completed", run.error
    assert run.result["candidates"][0]["data"]["business_domains"] == ["urban_sewage"]
    assert any("业务领域：" in w and "保留此前" in w for w in run.result["warnings"])


def test_cancellation_discards_late_model_output(context, monkeypatch):
    run = make_run(context)
    def model(*args, **kwargs):
        ResearchRun.objects.filter(pk=run.pk).update(status="paused", lease_token=None)
        return extract(*args, **kwargs)
    monkeypatch.setattr("enterprises.agent.model_json", model)
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "paused" and not run.result
    assert not ResearchArtifact.objects.filter(run=run).exists()


def test_budget_stops_before_more_requests_and_keeps_draft(context, monkeypatch):
    config = context[2]
    config.agent_max_reads = 1
    config.save()
    monkeypatch.setattr("enterprises.agent.model_json", extract)
    run = make_run(context, introduction=TEXT + "介绍。" * 1500 + TEXT)
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "completed" and run.usage["reads"] == 1
    assert any("预算" in w for w in run.result["warnings"])


def test_resume_uses_checkpoint_and_does_not_repeat_successful_extraction(context, monkeypatch):
    counts = {"extract": 0, "plan": 0}
    def model(*args, **kwargs):
        if args[2] is Decision:
            counts["plan"] += 1
            if counts["plan"] == 1:
                raise ValueError("synthetic failure")
            return Decision(action="finish", reason="资料已足够")
        counts["extract"] += 1
        return extract(*args, **kwargs)
    monkeypatch.setattr("enterprises.agent.model_json", model)
    run = make_run(context, introduction=TEXT + "介绍。" * 1500)
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "failed" and run.checkpoint["phase"] == "decide"
    assert context[3].post(f"/api/v1/enterprise-research/{run.pk}/resume").status_code == 200
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "completed" and counts["extract"] == 1
    process_research(str(run.pk))
    assert counts["extract"] == 1


def test_profile_or_config_change_blocks_resume_and_old_draft_confirmation(context, monkeypatch):
    monkeypatch.setattr("enterprises.agent.model_json", extract)
    run = make_run(context)
    process_research(str(run.pk))
    profile = context[1]
    profile.revision += 1
    profile.save()
    response = context[3].patch(f"/api/v1/enterprises/{profile.pk}", {"name": NAME, "revision": profile.revision, "data": {}, "run_id": str(run.pk)}, format="json")
    assert response.status_code == 409
    ResearchRun.objects.filter(pk=run.pk).update(status="paused")
    assert context[3].post(f"/api/v1/enterprise-research/{run.pk}/resume").status_code == 409


def test_pilot_is_explicit_and_other_users_cannot_control_run(context):
    run = make_run(context)
    stranger = APIClient()
    stranger.force_authenticate(User.objects.create_user("stranger-agent"))
    assert stranger.post(f"/api/v1/enterprise-research/{run.pk}/stop").status_code == 403
    context[2].agent_enabled = False
    context[2].save()
    assert snapshot_for(context[1]) == {}
    assert snapshot_for(None) == {}


def test_model_cannot_invent_read_target(context, monkeypatch):
    def model(*args, **kwargs):
        if args[2] is Decision:
            return Decision(action="read", option_id=999, reason="任意指令")
        return extract(*args, **kwargs)
    monkeypatch.setattr("enterprises.agent.model_json", model)
    run = make_run(context, introduction=TEXT + "介绍。" * 1500)
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "completed" and run.usage["reads"] == 1


@pytest.mark.parametrize("new_company", [False, True])
def test_all_companies_can_create_resume_and_confirm_drafts(context, monkeypatch, new_company):
    user, profile, config, client = context
    config.agent_all_organizations = True
    config.agent_organizations = []
    config.save()
    monkeypatch.setattr("enterprises.views.get_ai_profile", lambda *a: AIProfile("enterprise", "http://ollama:11434/v1", "", "test", True))
    monkeypatch.setattr("enterprises.agent.model_json", extract)
    if new_company:
        user = User.objects.create_user("new-agent-customer", is_staff=True, is_superuser=True)
        client.force_authenticate(user)
    payload = {"kind": "company", "name": NAME, "source_mode": "text", "introduction": TEXT}
    if not new_company:
        payload["profile_id"] = str(profile.pk)
    response = client.post("/api/v1/enterprise-research", payload, format="json")
    assert response.status_code == 202, response.data
    run = ResearchRun.objects.get(pk=response.data["id"])
    assert run.agent_snapshot
    assert run.agent_snapshot["profile_revision"] == (None if new_company else profile.revision)
    assert client.post(f"/api/v1/enterprise-research/{run.pk}/stop").status_code == 200
    assert client.post(f"/api/v1/enterprise-research/{run.pk}/resume").status_code == 200
    stranger = APIClient()
    stranger.force_authenticate(User.objects.create_user("other-global-agent"))
    assert stranger.get(f"/api/v1/enterprise-research/{run.pk}").status_code == 404
    assert stranger.post(f"/api/v1/enterprise-research/{run.pk}/resume").status_code == 403
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "completed", run.error
    payload = {"name": NAME, "data": run.result["candidates"][0]["data"], "run_id": str(run.pk)}
    if new_company:
        assert not EnterpriseProfile.objects.filter(organization__membership__user=user).exists()
        result = client.post("/api/v1/enterprises", payload, format="json")
        assert result.status_code == 201, result.data
    else:
        profile.refresh_from_db()
        assert profile.data == {"business_summary": "保留人工信息"}
        payload["revision"] = profile.revision
        result = client.patch(f"/api/v1/enterprises/{profile.pk}", payload, format="json")
        assert result.status_code == 200, result.data


def test_all_companies_still_requires_editor_and_obeys_disabled_scope(context, monkeypatch):
    user, profile, config, client = context
    config.agent_all_organizations = True
    config.agent_organizations = []
    config.save()
    run = make_run(context)
    Membership.objects.filter(user=user, organization=profile.organization).update(role="member")
    monkeypatch.setattr("enterprises.agent.model_json", lambda *a, **k: pytest.fail("成员无编辑权限，不应调用模型"))
    process_research(str(run.pk))
    run.refresh_from_db()
    assert run.status == "paused"
    assert client.post(f"/api/v1/enterprise-research/{run.pk}/resume").status_code == 403
    newcomer_run = ResearchRun.objects.create(user=user, fingerprint=uuid.uuid4().hex,
        inputs={"name": NAME, "source_mode": "text", "introduction": TEXT}, agent_snapshot=snapshot_for(None))
    config.agent_all_organizations = False
    config.save()
    assert snapshot_for(profile) == {} and snapshot_for(None) == {}
    process_research(str(newcomer_run.pk))
    newcomer_run.refresh_from_db()
    assert newcomer_run.status == "paused"
    assert client.post(f"/api/v1/enterprise-research/{newcomer_run.pk}/resume").status_code == 409


def test_global_scope_setting_requires_config_admin_and_master_switch(context):
    user, profile, config, client = context
    user.is_staff = user.is_superuser = False
    user.save()
    endpoint = "/api/v1/admin/enterprise-research-settings"
    assert client.patch(endpoint, {"agent_all_organizations": True}, format="json").status_code == 403
    user.is_staff = True
    user.save()
    assert client.patch(endpoint, {"agent_all_organizations": True}, format="json").status_code == 403
    user.is_superuser = True
    user.save()
    result = client.patch(endpoint, {"agent_all_organizations": True}, format="json")
    assert result.status_code == 200 and result.data["agent_all_organizations"] is True
    assert snapshot_for(profile) and snapshot_for(None)
    config.refresh_from_db()
    config.agent_enabled = False
    config.save()
    assert snapshot_for(profile) == {} and snapshot_for(None) == {}
