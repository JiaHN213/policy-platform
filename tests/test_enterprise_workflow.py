from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest
from accounts.models import Membership, Organization, User
from core.ai_runtime import get_ai_profile
from core.ai_usage import request_json
from core.models import AICall, AIModelProfile
from django.utils import timezone
from enterprises import tasks, workflow
from enterprises.match_analysis import Analysis
from enterprises.models import EnterpriseProfile, EnterpriseProject, ResearchRun, ResearchSettings
from enterprises.runtime import task_summary
from policies.models import Opportunity, Policy
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(monkeypatch):
    user = User.objects.create_user("workflow-user", is_staff=True, is_superuser=True)
    org = Organization.objects.create(name="流程测试企业")
    Membership.objects.create(user=user, organization=org, role="admin")
    profile = EnterpriseProfile.objects.create(organization=org, confirmed_at=timezone.now(), data={"business_domains": ["urban_sewage"]})
    ResearchSettings.objects.create()
    AIModelProfile.objects.create(purpose="enterprise", model="test", base_url="https://example.com/v1", api_key="test")
    policies = []
    for i in range(4):
        policy = Policy.objects.create(source_key=f"flow-{i}", title=f"污水处理支持{i}", body="支持城镇污水处理企业进行技术改造。",
            status="published", source_grade="L1", validity_status="effective", publication_date=date(2026, 1, i + 1), source_url=f"https://example.gov.cn/{i}", business_domains=["urban_sewage"])
        Opportunity.objects.create(policy=policy, title="项目改造支持", category="fiscal", status="open", evidence_policy=policy,
            evidence_version=policy.version, evidence_quote=policy.body, verification_status="verified")
        policies.append(policy)
    monkeypatch.setattr(tasks, "enqueue", lambda run: None)
    monkeypatch.setattr("enterprises.retrieval.configured", lambda: False)
    def model(*args, **kwargs):
        client = MagicMock()
        client.post.return_value.json.return_value = {"usage": {"prompt_tokens": 10, "completion_tokens": 10}}
        request_json(client, get_ai_profile("enterprise"), "https://example.com", headers={}, payload={})
        return Analysis()
    monkeypatch.setattr("enterprises.match_analysis.model_json", model)
    client = APIClient()
    client.force_authenticate(user)
    return user, profile, policies, client


def drain(root):
    for _ in range(15):
        root.refresh_from_db()
        if root.status == "queued":
            tasks.process_research(root.pk)
        child = root.children.filter(status="queued").first()
        if child:
            tasks.process_research(child.pk)
        root.refresh_from_db()
        if root.status in {"completed", "failed", "paused"}:
            return
    pytest.fail("Workflow did not terminate within bounded steps")


def test_confirm_then_retrieve_and_analyze_three_with_one_root(setup):
    user, profile, _, client = setup
    response = client.patch(f"/api/v1/enterprises/{profile.pk}", {"name": profile.organization.name, "data": profile.data, "revision": profile.revision, "start_matching": True}, format="json")
    assert response.status_code == 200
    root = ResearchRun.objects.get(pk=response.data["matching_task"]["id"])
    drain(root)
    assert root.status == "completed"
    assert root.children.count() == 3
    assert root.children.filter(status="completed").count() == 3
    assert AICall.objects.filter(task__parent=root).count() == 3
    report = client.get(f"/api/v1/enterprise-research/{root.pk}").data["result"]
    assert len(report["items"]) == 3 and "candidate_count" not in report
    internal = client.get(f"/api/v1/enterprise-research/{root.pk}/task").data["workflow"]
    assert internal["candidate_count"] == 4 and internal["completed_count"] == 3
    assert task_summary(root)["request_count"] == 3
    assert client.get("/api/v1/enterprise-research/tasks").data["count"] == 1
    assert all(call.step_id for call in AICall.objects.all())
    assert profile.organization.membership_set.get().user_id == user.pk


def test_same_active_request_is_idempotent_and_children_are_serial(setup):
    user, profile, _, _ = setup
    root = workflow.start(user, profile)
    assert workflow.start(user, profile).pk == root.pk
    tasks.process_research(root.pk)
    root.refresh_from_db()
    assert root.status == "waiting" and root.children.filter(status="queued").count() == 1
    tasks.process_research(root.pk)
    assert root.children.count() == 3


def test_stop_inflight_drops_result_and_resume_preserves_completed(setup, monkeypatch):
    user, profile, _, client = setup
    root = workflow.start(user, profile)
    tasks.process_research(root.pk)
    first = root.children.get(status="queued")
    tasks.process_research(first.pk)
    tasks.process_research(root.pk)
    second = root.children.get(status="queued")
    original = tasks.explain_match
    def stopped(*args, **kwargs):
        value = original(*args, **kwargs)
        assert client.post(f"/api/v1/enterprise-research/{root.pk}/stop").status_code == 200
        return value
    monkeypatch.setattr(tasks, "explain_match", stopped)
    tasks.process_research(second.pk)
    second.refresh_from_db()
    root.refresh_from_db()
    assert second.status == root.status == "paused" and not second.result
    assert root.children.get(pk=first.pk).status == "completed"
    assert not root.children.filter(status__in=["queued", "running"]).exists()
    monkeypatch.setattr(tasks, "explain_match", original)
    assert client.post(f"/api/v1/enterprise-research/{root.pk}/resume").status_code == 200
    drain(root)
    assert root.status == "completed" and root.children.count() == 3
    assert AICall.objects.filter(task=first).count() == 1
    assert AICall.objects.count() == 4


@pytest.mark.parametrize("change", ["profile", "project", "permission", "model", "budget"])
def test_changed_inputs_prevent_resume(setup, change):
    user, profile, _, client = setup
    project = EnterpriseProject.objects.create(profile=profile, name="改造", data=profile.data)
    root = workflow.start(user, profile, project=project)
    client.post(f"/api/v1/enterprise-research/{root.pk}/stop")
    if change == "profile":
        profile.revision += 1
        profile.save()
    elif change == "project":
        project.revision += 1
        project.save()
    elif change == "permission":
        Membership.objects.filter(user=user).update(active=False)
    elif change == "model":
        AIModelProfile.objects.update(model="changed")
    else:
        ResearchSettings.objects.update(workflow_max_calls=2)
    assert client.post(f"/api/v1/enterprise-research/{root.pk}/resume").status_code in {404, 409}
    assert AICall.objects.count() == 0


def test_withdrawn_child_hidden_and_failure_does_not_block_other_policies(setup):
    user, profile, _, client = setup
    root = workflow.start(user, profile)
    tasks.process_research(root.pk)
    child = root.children.get(status="queued")
    Policy.objects.filter(pk=child.inputs["policy_id"]).update(status="withdrawn")
    drain(root)
    assert root.status == "failed"
    assert root.children.filter(status="completed").count() == 2
    payload = client.get(f"/api/v1/enterprise-research/{root.pk}").data["result"]
    assert all(row["id"] != str(child.pk) for row in payload["items"])
    assert len(payload["items"]) == 2
    assert client.get(f"/api/v1/enterprise-research/{child.pk}").status_code == 409


def test_shared_request_budget_blocks_resume(setup):
    user, profile, _, client = setup
    ResearchSettings.objects.update(workflow_max_calls=1)
    root = workflow.start(user, profile)
    drain(root)
    assert root.status == "failed" and AICall.objects.count() == 1
    assert root.usage["calls"] == 1
    assert client.post(f"/api/v1/enterprise-research/{root.pk}/resume").status_code == 400


def test_filters_no_candidates_no_model_and_foreign_profile_denied(setup):
    user, profile, _, client = setup
    response = client.post(f"/api/v1/enterprises/{profile.pk}/matching-workflow", {"filters": {"q": "完全没有的词"}}, format="json")
    assert response.status_code == 202
    root = ResearchRun.objects.get(pk=response.data["id"])
    drain(root)
    assert root.status == "completed" and not root.children.exists() and not AICall.objects.exists()
    client.force_authenticate(User.objects.create_user("foreign-workflow"))
    assert client.post(f"/api/v1/enterprises/{profile.pk}/matching-workflow", {}, format="json").status_code == 404
    assert client.get(f"/api/v1/enterprise-research/{root.pk}/task").status_code == 403


def test_missing_model_does_not_rollback_confirmed_profile(setup):
    _, profile, _, client = setup
    AIModelProfile.objects.update(enabled=False)
    response = client.patch(f"/api/v1/enterprises/{profile.pk}", {"name": profile.organization.name, "data": profile.data, "revision": profile.revision, "start_matching": True}, format="json")
    assert response.status_code == 200 and response.data["matching_task"]["id"] is None
    profile.refresh_from_db()
    assert profile.revision == 2 and not ResearchRun.objects.exists()


def test_dispatcher_recovers_lost_parent_wakeup_and_abandoned_child(setup):
    user, profile, _, _ = setup
    root = workflow.start(user, profile)
    tasks.process_research(root.pk)
    child = root.children.get(status="queued")
    ResearchRun.objects.filter(pk=child.pk).update(status="running", started_at=timezone.now() - timedelta(minutes=13))
    tasks.dispatch_research()
    root.refresh_from_db()
    assert root.status == "queued"
    drain(root)
    assert root.status == "failed" and not root.children.filter(status="running").exists()


def test_cannot_control_child_outside_parent(setup):
    user, profile, _, client = setup
    root = workflow.start(user, profile)
    tasks.process_research(root.pk)
    child = root.children.get(status="queued")
    assert client.post(f"/api/v1/enterprise-research/{child.pk}/stop").status_code == 400
    assert client.post(f"/api/v1/enterprise-research/{child.pk}/resume").status_code == 400


def test_elapsed_budget_and_configured_policy_limit(setup):
    user, profile, _, _ = setup
    ResearchSettings.objects.update(workflow_max_policies=2, workflow_max_seconds=60)
    root = workflow.start(user, profile)
    tasks.process_research(root.pk)
    first = root.children.get(status="queued")
    tasks.process_research(first.pk)
    ResearchRun.objects.filter(pk=first.pk).update(usage={"workflow_seconds": 61})
    drain(root)
    assert root.children.count() == 2 and root.status == "failed"
    assert AICall.objects.count() == 1


def test_plain_confirmation_does_not_silently_start_ai(setup):
    _, profile, _, client = setup
    response = client.patch(f"/api/v1/enterprises/{profile.pk}", {"name": profile.organization.name, "data": profile.data, "revision": profile.revision}, format="json")
    assert response.status_code == 200 and response.data["matching_task"] is None
    assert not ResearchRun.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_start_creates_only_one_parent_on_postgres(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections, connection
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locking")
    user, profile, _, _ = setup
    barrier = Barrier(2)
    def launch():
        close_old_connections()
        try:
            actor = User.objects.get(pk=user.pk)
            target = EnterpriseProfile.objects.get(pk=profile.pk)
            barrier.wait(timeout=10)
            return workflow.start(actor, target).pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(launch) for _ in range(2)]
        ids = [future.result(timeout=20) for future in futures]
    assert ids[0] == ids[1] and ResearchRun.objects.filter(kind="workflow").count() == 1


def test_stage34_gap_research_is_grounded_draft_until_confirmation(setup, monkeypatch):
    from enterprises.research import CompanyDraft
    user, profile, _, client = setup
    profile.data = {}
    profile.research_method = "materials"
    profile.save()
    ResearchSettings.objects.update(agent_enabled=True, agent_all_organizations=True)
    source = ResearchRun.objects.create(user=user, profile=profile, kind="company", status="completed", fingerprint="material",
        inputs={"name": profile.organization.name, "source_mode": "text", "introduction": "流程测试企业主营业务为城镇污水处理。"})
    def draft(instruction, data, schema, **kwargs):
        assert set(data["fields"]) == {"business_domains"}
        model = MagicMock()
        model.post.return_value.json.return_value = {"usage": {"prompt_tokens": 8, "completion_tokens": 6}}
        request_json(model, get_ai_profile("enterprise"), "https://example.com", headers={}, payload={})
        return CompanyDraft.model_validate({"candidates": [{"name": profile.organization.name,
            "identity_evidence": {"source_id": 1, "quote": "流程测试企业"},
            "fields": [{"field": "business_domains", "value": ["urban_sewage"], "evidence": [{"source_id": 1, "quote": "城镇污水处理"}]}]}]})
    monkeypatch.setattr("enterprises.agent.model_json", draft)
    monkeypatch.setattr("enterprises.agent.search_sources", lambda *a, **k: pytest.fail("Private material must not trigger search"))
    root = workflow.start(user, profile, source_run=source.pk)
    drain(root)
    child = root.children.get(kind="company")
    assert child.status == "completed" and child.result["candidates"][0]["data"] == {"business_domains": ["urban_sewage"]}
    profile.refresh_from_db()
    assert not profile.data
    assert workflow.report(root)["gap_fill"]["can_open"]
    assert root.usage["calls"] == 1 and AICall.objects.get().task_id == child.pk
    confirmed = client.patch(f"/api/v1/enterprises/{profile.pk}", {"name": profile.organization.name,
        "data": child.result["candidates"][0]["data"], "revision": profile.revision, "run_id": str(child.pk), "start_matching": True}, format="json")
    assert confirmed.status_code == 200
    successor = ResearchRun.objects.get(pk=confirmed.data["matching_task"]["id"])
    assert successor.inputs["fill_gaps"] is False and successor.inputs["profile_revision"] == 2
    profile.refresh_from_db()
    assert profile.data["business_domains"] == ["urban_sewage"]


def test_stage34_no_source_no_network_no_invented_financials(setup):
    from enterprises.gap_fill import gaps
    user, profile, _, _ = setup
    profile.research_method = "materials"
    profile.data = {}
    profile.save()
    ResearchSettings.objects.update(agent_enabled=True, agent_all_organizations=True)
    root = workflow.start(user, profile)
    drain(root)
    assert not root.children.exists()
    assert "没有可继续读取" in workflow.report(root)["gap_fill"]["note"]
    missing = gaps(profile, None, [{"supplement_fields": ["enterprise.annual_revenue_wan"]}])
    assert not next(row for row in missing if row["field"] == "enterprise.annual_revenue_wan")["researchable"]


def test_stage34_valid_results_reused_without_new_model_calls(setup):
    user, profile, _, _ = setup
    first = workflow.start(user, profile)
    drain(first)
    assert AICall.objects.count() == 3
    second = workflow.start(user, profile)
    drain(second)
    assert second.status == "completed" and second.usage.get("calls", 0) == 0
    assert AICall.objects.count() == 3 and workflow.report(second)["reused_count"] == 3


@pytest.mark.parametrize("change", ["policy", "profile", "model", "ttl", "owner"])
def test_stage34_stale_or_other_user_results_not_reused(setup, change):
    user, profile, policies, _ = setup
    first = workflow.start(user, profile)
    drain(first)
    if change == "policy":
        Policy.objects.filter(pk__in=[p.pk for p in policies]).update(version=2)
        Opportunity.objects.all().update(evidence_version=2)
    elif change == "profile":
        profile.revision += 1
        profile.save()
    elif change == "model":
        AIModelProfile.objects.update(model="new-model")
    elif change == "ttl":
        first.children.update(finished_at=timezone.now() - timedelta(days=5))
    else:
        user = User.objects.create_user("another-member")
        Membership.objects.create(user=user, organization=profile.organization, role="member")
    second = workflow.start(user, profile)
    drain(second)
    assert second.status == "completed" and workflow.report(second)["reused_count"] == 0
    assert AICall.objects.count() == 6


def test_stage34_parallel_slots_and_stop_are_bounded(setup):
    user, profile, _, client = setup
    ResearchSettings.objects.update(workflow_concurrency=2)
    root = workflow.start(user, profile)
    tasks.process_research(root.pk)
    assert root.children.filter(status="queued").count() == 2
    first = root.children.filter(status="queued").first()
    tasks.process_research(first.pk)
    tasks.process_research(root.pk)
    assert root.children.filter(status="queued").count() == 2
    client.post(f"/api/v1/enterprise-research/{root.pk}/stop")
    assert not root.children.filter(status__in=["queued", "running"]).exists()
    assert root.children.get(pk=first.pk).status == "completed"


def test_stage34_daily_budget_across_roots_not_reset_by_restart(setup):
    user, profile, _, _ = setup
    ResearchSettings.objects.update(workflow_daily_calls=1, workflow_cache_hours=0)
    first = workflow.start(user, profile)
    drain(first)
    second = workflow.start(user, profile)
    drain(second)
    assert AICall.objects.count() == 1
    assert second.status == "failed"
    assert any("今天" in row.error for row in second.children.all())


def test_stage34_deleted_project_does_not_silently_widen_matching(setup):
    from enterprises.views import start_after_confirmation
    user, profile, _, _ = setup
    project = EnterpriseProject.objects.create(profile=profile, name="原项目", data=profile.data)
    root = workflow.start(user, profile, project=project)
    child = ResearchRun.objects.create(parent=root, user=user, profile=profile, kind="company", status="completed", fingerprint="gaps")
    project.delete()
    start_after_confirmation(user, profile, {"run_id": child.pk, "start_matching": True})
    assert profile.matching_task["id"] is None and "项目已删除" in profile.matching_task["message"]
    assert ResearchRun.objects.filter(kind="workflow").count() == 1


def test_stage34_transient_retry_waits_and_preserves_budget(setup, monkeypatch):
    import httpx
    user, profile, _, _ = setup
    root = workflow.start(user, profile)
    tasks.process_research(root.pk)
    child = root.children.get(status="queued")
    original = tasks.explain_match
    def timeout(*args, **kwargs):
        mock = MagicMock()
        mock.post.side_effect = httpx.ReadTimeout("temporary")
        request_json(mock, get_ai_profile("enterprise"), "https://example.com", headers={}, payload={})
    monkeypatch.setattr(tasks, "explain_match", timeout)
    tasks.process_research(child.pk)
    child.refresh_from_db()
    assert child.status == "queued" and child.usage["auto_retries"] == 1 and child.retry_at > timezone.now()
    tasks.process_research(child.pk)
    assert AICall.objects.count() == 1
    monkeypatch.setattr(tasks, "explain_match", original)
    ResearchRun.objects.filter(pk=child.pk).update(retry_at=timezone.now() - timedelta(seconds=1))
    drain(root)
    assert root.status == "completed" and root.usage["calls"] == 4


@pytest.mark.django_db(transaction=True)
def test_stage34_parallel_request_reservations_do_not_exceed_shared_limit(setup):
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections, connection
    from enterprises.agent import Halted
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL row locking")
    user, profile, _, _ = setup
    ResearchSettings.objects.update(workflow_concurrency=2, workflow_max_calls=1)
    root = workflow.start(user, profile)
    tasks.process_research(root.pk)
    children = list(root.children.filter(status="queued"))
    tokens = [uuid.uuid4() for _ in children]
    for child, token in zip(children, tokens, strict=True):
        ResearchRun.objects.filter(pk=child.pk).update(status="running", lease_token=token, started_at=timezone.now())
    barrier = Barrier(2)
    def reserve(child_id, token):
        close_old_connections()
        try:
            child = ResearchRun.objects.get(pk=child_id)
            barrier.wait(timeout=10)
            try:
                workflow.reserve_request(child, token)
                return True
            except Halted:
                return False
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve, child.pk, token) for child, token in zip(children, tokens, strict=True)]
        assert sum(future.result(timeout=20) for future in futures) == 1
    root.refresh_from_db()
    assert root.usage["calls"] == 1
