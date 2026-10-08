import uuid
from unittest.mock import MagicMock

import pytest
from accounts.models import Membership, Organization, User
from core.ai_runtime import get_ai_profile
from core.ai_usage import request_json
from core.models import AICall, AIModelProfile
from django.utils import timezone
from enterprises import tasks
from enterprises.models import EnterpriseProfile, ResearchRun, ResearchSettings, ResearchStep
from enterprises.runtime import snapshot, tracking
from policies.models import Policy
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def project_run():
    user = User.objects.create_user("runtime-owner", is_staff=True, is_superuser=True)
    organization = Organization.objects.create(name="运行研究企业")
    Membership.objects.create(user=user, organization=organization, role="admin")
    profile = EnterpriseProfile.objects.create(organization=organization, data={"business_domains": ["water_supply"]})
    config = ResearchSettings.objects.create()
    AIModelProfile.objects.create(purpose="enterprise", model="test-model", base_url="https://example.com/v1", api_key="test")
    return ResearchRun.objects.create(user=user, profile=profile, kind="project", inputs={"description": "供水设备改造"}, fingerprint="task", runtime_snapshot=snapshot(profile, config))


def api_client(run):
    client = APIClient()
    client.force_authenticate(run.user)
    return client


def model_attempt():
    client = MagicMock()
    client.post.return_value.json.return_value = {"usage": {"prompt_tokens": 20, "completion_tokens": 10}}
    return client


def test_project_execution_records_steps_and_http_attempts(project_run, monkeypatch):
    def draft(_):
        request_json(model_attempt(), get_ai_profile("enterprise"), "https://example.com", headers={}, payload={})
        return {"name": "供水改造", "data": {}}
    monkeypatch.setattr(tasks, "project_draft", draft)
    tasks.process_research(project_run.pk)
    project_run.refresh_from_db()
    assert project_run.status == "completed"
    assert project_run.steps.get().status == "completed"
    call = AICall.objects.get()
    assert call.task_id == project_run.pk and call.step_id == project_run.steps.get().pk
    client = api_client(project_run)
    detail = client.get(f"/api/v1/enterprise-research/{project_run.pk}/task").data
    assert detail["request_count"] == 1 and detail["completed_steps"] == 1
    assert "inputs" not in detail and "result" not in detail
    assert detail["requests"][0]["input_tokens"] == 20


def test_stop_during_http_discards_result_but_keeps_usage(project_run, monkeypatch):
    client = api_client(project_run)
    model = model_attempt()
    def post(*args, **kwargs):
        assert client.post(f"/api/v1/enterprise-research/{project_run.pk}/stop").status_code == 200
        return model.post.return_value
    model.post.side_effect = post
    def draft(_):
        request_json(model, get_ai_profile("enterprise"), "https://example.com", headers={}, payload={})
        return {"name": "不应应用的结果"}
    monkeypatch.setattr(tasks, "project_draft", draft)
    tasks.process_research(project_run.pk)
    project_run.refresh_from_db()
    assert project_run.status == "paused" and not project_run.result
    assert project_run.steps.get().status == "interrupted"
    assert AICall.objects.filter(task=project_run).count() == 1
    monkeypatch.setattr("enterprises.views.enqueue", lambda run: None)
    result = client.post(f"/api/v1/enterprise-research/{project_run.pk}/resume")
    assert result.status_code == 200 and str(result.data["id"]) == str(project_run.pk)
    assert client.post(f"/api/v1/enterprise-research/{project_run.pk}/resume").status_code == 400


@pytest.mark.parametrize("change", ["profile", "model", "membership"])
def test_resume_rejects_changed_inputs_and_permissions(project_run, monkeypatch, change):
    project_run.status = "failed"
    project_run.save()
    if change == "profile":
        EnterpriseProfile.objects.filter(pk=project_run.profile_id).update(revision=2)
    elif change == "model":
        AIModelProfile.objects.filter(purpose="enterprise").update(model="different")
    else:
        Membership.objects.filter(user=project_run.user).update(active=False)
    monkeypatch.setattr("enterprises.views.enqueue", lambda run: pytest.fail("must not dispatch"))
    assert api_client(project_run).post(f"/api/v1/enterprise-research/{project_run.pk}/resume").status_code in {404, 409}


def test_task_metadata_is_private_and_invalid_filters_rejected(project_run):
    client = api_client(project_run)
    assert client.get("/api/v1/enterprise-research/tasks?kind=project").data["count"] == 1
    assert client.get("/api/v1/enterprise-research/tasks?page=-1").status_code == 400
    assert client.get("/api/v1/enterprise-research/tasks?status=other").status_code == 400
    client.force_authenticate(User.objects.create_user("other-owner", is_staff=True, is_superuser=True))
    assert client.get("/api/v1/enterprise-research/tasks").data["count"] == 0
    assert client.get(f"/api/v1/enterprise-research/{project_run.pk}/task").status_code == 404


def test_old_execution_never_closes_new_execution_steps(project_run):
    old, new = uuid.uuid4(), uuid.uuid4()
    ResearchRun.objects.filter(pk=project_run.pk).update(status="running", lease_token=old)
    with tracking(project_run, old) as execution:
        execution.progress("旧执行")
        ResearchRun.objects.filter(pk=project_run.pk).update(lease_token=new)
        ResearchStep.objects.create(run=project_run, sequence=2, label="新执行", execution_token=new)
    assert project_run.steps.get(sequence=1).status == "interrupted"
    assert project_run.steps.get(sequence=2).status == "running"


def test_explanation_stale_results_hidden_and_resume_blocked(project_run):
    policy = Policy.objects.create(title="供水支持", body="支持供水项目建设", publication_date=timezone.now().date(), status="published",
        source_key="runtime-policy", content_hash="runtime-policy", source_grade="L1", document_type="policy", geographic_level="national")
    project_run.kind, project_run.status = "explanation", "failed"
    project_run.inputs = {"policy_id": str(policy.pk), "policy_version": 1, "profile_revision": 1}
    project_run.save()
    ResearchStep.objects.create(run=project_run, sequence=1, label="已读取的政策内容", detail="旧政策内容")
    Policy.objects.filter(pk=policy.pk).update(status="withdrawn")
    client = api_client(project_run)
    result = client.get(f"/api/v1/enterprise-research/{project_run.pk}/task")
    assert result.status_code == 200 and not result.data["can_open"] and result.data["steps"] == []
    assert client.post(f"/api/v1/enterprise-research/{project_run.pk}/resume").status_code == 409


def test_match_graph_propagates_task_and_step_context(project_run, monkeypatch):
    from enterprises.match_analysis import Analysis
    policy = Policy.objects.create(title="供水支持", body="支持供水项目建设", publication_date=timezone.now().date(), status="published",
        source_key="graph-policy", content_hash="graph-policy", source_grade="L1", document_type="policy", geographic_level="national")
    project_run.kind = "explanation"
    project_run.inputs = {"policy_id": str(policy.pk), "policy_version": 1, "profile_revision": 1}
    project_run.save()
    def analysis(*args, **kwargs):
        request_json(model_attempt(), get_ai_profile("enterprise"), "https://example.com", headers={}, payload={})
        return Analysis()
    monkeypatch.setattr("enterprises.match_analysis.model_json", analysis)
    tasks.process_research(project_run.pk)
    project_run.refresh_from_db()
    assert project_run.status == "completed"
    call = AICall.objects.get()
    assert call.task_id == project_run.pk
    assert "分析相关条款" in call.step.label
    assert project_run.steps.filter(status="running").count() == 0


def test_bounded_agent_keeps_its_own_steps_and_links_requests(project_run, monkeypatch):
    from enterprises.agent import snapshot_for
    from enterprises.research import CompanyDraft
    config = ResearchSettings.objects.get()
    config.agent_enabled = config.agent_all_organizations = True
    config.save()
    project_run.kind = "company"
    project_run.inputs = {"source_mode": "text", "name": "运行研究企业", "introduction": "本企业从事供水设施运营和设备改造，提供专业供水服务。"}
    project_run.agent_snapshot = snapshot_for(project_run.profile)
    project_run.runtime_snapshot = snapshot(project_run.profile, config)
    project_run.save()
    def extraction(*args, **kwargs):
        request_json(model_attempt(), get_ai_profile("enterprise"), "https://example.com", headers={}, payload={})
        return CompanyDraft(candidates=[])
    monkeypatch.setattr("enterprises.agent.model_json", extraction)
    tasks.process_research(project_run.pk)
    project_run.refresh_from_db()
    assert project_run.status == "completed"
    call = AICall.objects.get()
    assert call.task_id == project_run.pk and call.step.tool == "model"
