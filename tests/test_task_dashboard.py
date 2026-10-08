from datetime import datetime, time, timedelta

import pytest
from accounts.models import Membership, Organization, User
from core.models import AICall
from django.utils import timezone
from enterprises.models import EnterpriseProfile, ResearchRun
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    user = User.objects.create_user("dashboard-owner", is_staff=True, is_superuser=True)
    org = Organization.objects.create(name="看板测试企业")
    Membership.objects.create(user=user, organization=org, role="admin")
    profile = EnterpriseProfile.objects.create(organization=org)
    client = APIClient()
    client.force_authenticate(user)
    return user, profile, client


def run(owner, **kwargs):
    return ResearchRun.objects.create(user=owner[0], profile=owner[1], fingerprint=str(ResearchRun.objects.count()), **kwargs)


def call(task, **kwargs):
    return AICall.objects.create(task=task, purpose="enterprise", model="test", status="received", duration_ms=2000,
        input_tokens=None, output_tokens=None, estimated_cost=None, currency="CNY", input_price=None, output_price=None, **kwargs)


def test_counts_do_not_duplicate_parents_calls_or_failures(owner):
    root = run(owner, kind="workflow", status="failed", error="部分解读未完成")
    run(owner, parent=root, kind="explanation", status="failed", error="模型连接超时")
    cached = run(owner, parent=root, kind="explanation", status="completed", checkpoint={"reused_from": "original"})
    other = run(owner, parent=root, kind="explanation", status="completed")
    call(root)
    call(other)
    failed_call = call(other)
    AICall.objects.filter(pk=failed_call.pk).update(status="timeout")
    assert cached.model_requests.count() == 0
    success = run(owner, kind="company", status="completed")
    ResearchRun.objects.filter(pk=success.pk).update(finished_at=success.created_at + timedelta(seconds=120))
    run(owner, status="queued", retry_at=timezone.now() + timedelta(minutes=1))
    run(owner, status="paused")
    result = owner[2].get("/api/v1/enterprise-research/dashboard?days=7").data
    assert result["total"] == 4
    assert result["statuses"]["failed"] == 1 and result["failure_rate"] == 50
    assert result["average_completion_seconds"] == 120
    assert result["calls"] == {"total": 3, "failed": 1, "average_ms": 2000}
    assert result["reuse"] == {"count": 1, "completed_explanations": 2, "rate": 50}
    assert result["retry_waiting"] == 1
    assert len(result["failure_reasons"]) == 1
    assert result["failure_reasons"][0]["reason"] == "服务超时或连接异常"
    assert result["failure_reasons"][0]["count"] == 1
    assert sum(day["total"] for day in result["trend"]) == 4


def test_access_filter_applies_to_roots_and_children(owner):
    root = run(owner, status="completed")
    call(root)
    outsider = User.objects.create_user("dashboard-other")
    foreign = ResearchRun.objects.create(user=outsider, profile=owner[1], status="failed", error="private message")
    call(foreign)
    assert owner[2].get("/api/v1/enterprise-research/dashboard").data["total"] == 1
    Membership.objects.filter(user=owner[0]).update(active=False)
    response = owner[2].get("/api/v1/enterprise-research/dashboard")
    assert response.data["total"] == 0 and response.data["calls"]["total"] == 0
    assert b"private" not in response.content
    owner[2].force_authenticate(None)
    assert owner[2].get("/api/v1/enterprise-research/dashboard").status_code in {401, 403}


def test_local_day_cohort_matches_list_and_includes_later_child_calls(owner):
    midnight = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    old = run(owner, kind="company")
    ResearchRun.objects.filter(pk=old.pk).update(created_at=midnight - timedelta(microseconds=1))
    recent_child = run(owner, parent=old)
    call(recent_child)
    current = run(owner, kind="project")
    ResearchRun.objects.filter(pk=current.pk).update(created_at=midnight)
    for endpoint in ("dashboard", "tasks"):
        data = owner[2].get(f"/api/v1/enterprise-research/{endpoint}?days=1&kind=project").data
        assert data["total" if endpoint == "dashboard" else "count"] == 1
    assert owner[2].get("/api/v1/enterprise-research/dashboard?days=1").data["calls"]["total"] == 0
    assert owner[2].get("/api/v1/enterprise-research/dashboard?days=7").data["calls"]["total"] == 1


def test_empty_and_missing_historical_metrics_remain_unknown(owner):
    data = owner[2].get("/api/v1/enterprise-research/dashboard").data
    assert data["failure_rate"] is None and data["reuse"]["rate"] is None
    assert data["average_completion_seconds"] is None and len(data["trend"]) == 7
    run(owner, status="completed", finished_at=None)
    data = owner[2].get("/api/v1/enterprise-research/dashboard").data
    assert data["duration_samples"] == 0 and data["average_completion_seconds"] is None
    assert data["calls"]["average_ms"] is None


@pytest.mark.parametrize("query", ["days=0", "days=365", "days=abc", "kind=unknown"])
def test_invalid_scope_is_rejected(owner, query):
    for endpoint in ("dashboard", "tasks"):
        assert owner[2].get(f"/api/v1/enterprise-research/{endpoint}?{query}").status_code == 400


def test_status_filter_only_narrows_list_and_no_raw_failure_text_exposed(owner):
    run(owner, status="failed", error="private material SECRET123")
    run(owner, status="queued")
    assert owner[2].get("/api/v1/enterprise-research/tasks?days=7&status=failed").data["count"] == 1
    response = owner[2].get("/api/v1/enterprise-research/dashboard?days=7&status=failed")
    assert response.data["total"] == 2
    assert b"SECRET123" not in response.content
    assert response.data["failure_reasons"][0]["reason"] == "其他未完成原因"
