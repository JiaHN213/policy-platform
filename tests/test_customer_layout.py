from copy import deepcopy
from datetime import timedelta

import pytest
from accounts.models import Membership, Organization, User
from django.contrib.auth.models import Permission
from django.utils import timezone
from enterprises.models import EnterpriseProfile, EnterpriseProject, ResearchRun
from policies.models import Policy
from rest_framework.test import APIClient
from subscriptions.models import Subscription

pytestmark = pytest.mark.django_db


@pytest.fixture
def customer():
    user = User.objects.create_user("layout-customer")
    org = Organization.objects.create(name="PRIVATE_COMPANY")
    Membership.objects.create(user=user, organization=org, role="admin")
    profile = EnterpriseProfile.objects.create(organization=org, confirmed_at=timezone.now(), data={"business_domains": ["water_supply"]})
    client = APIClient()
    client.force_authenticate(user)
    return user, profile, client


def manager(client):
    user = User.objects.create_user("layout-manager", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="manage_system"))
    client.force_authenticate(user)
    return user


def test_customer_and_operator_cannot_read_execution_or_config(customer):
    user, profile, client = customer
    run = ResearchRun.objects.create(user=user, profile=profile)
    for staff in (False, True):
        user.is_staff = staff
        user.save(update_fields=["is_staff"])
        for path in ("enterprise-research/tasks", "enterprise-research/dashboard", f"enterprise-research/{run.pk}/task", "admin/task-monitor", "admin/task-monitor/dashboard"):
            assert client.get(f"/api/v1/{path}").status_code == 403
        for verb in ("stop", "resume"):
            assert client.post(f"/api/v1/enterprise-research/{run.pk}/{verb}").status_code == 403
        assert client.get("/api/v1/me").data["can_manage_system"] is False
    manager(client)
    assert client.get("/api/v1/me").data["can_manage_system"] is True


def test_monitor_is_redacted_and_does_not_grant_private_access(customer):
    user, profile, client = customer
    run = ResearchRun.objects.create(user=user, profile=profile, status="failed", inputs={"secret": "PRIVATE_INPUT"}, result={"secret": "PRIVATE_OUTPUT"}, error="PRIVATE_ERROR")
    manager(client)
    for path in ("admin/task-monitor", "admin/task-monitor/dashboard"):
        response = client.get(f"/api/v1/{path}")
        assert response.status_code == 200
        assert b"PRIVATE_" not in response.content
    assert client.get(f"/api/v1/enterprise-research/{run.pk}").status_code == 404
    assert client.get(f"/api/v1/enterprises/{profile.pk}").status_code == 404


@pytest.mark.parametrize("state", ["running", "failed", "completed"])
def test_business_result_has_no_execution_trace(customer, state):
    user, profile, client = customer
    run = ResearchRun.objects.create(user=user, profile=profile, kind="company", status=state,
        inputs={"name": "资料", "introduction": "PRIVATE_INPUT"}, stage="PRIVATE_STAGE", error="PRIVATE_ERROR",
        agent_snapshot={"model": "PRIVATE_MODEL"}, result={"candidates": [], "usage": {"calls": 3}})
    response = client.get(f"/api/v1/enterprise-research/{run.pk}")
    assert response.status_code == 200
    assert b"PRIVATE_" not in response.content
    assert not {"stage", "agent", "finished_at"} & response.data.keys()
    assert "usage" not in response.data["result"]
    assert response.data["status"] == {"running": "pending", "failed": "needs_information", "completed": "completed"}[state]


def test_home_separates_uncertain_and_does_not_show_expired_deadlines(customer, monkeypatch):
    _, profile, client = customer
    now = timezone.now()
    def row(id, level="high", group="priority", condition="consistent", deadline=None):
        return {"policy_id": id, "level": level, "recommendation_group": group, "conditions": {"status": condition}, "deadline": deadline, "retrieval": "private", "score": 1}
    rows = [row("good", deadline=(now + timedelta(days=2)).isoformat()), row("unknown", "insufficient", "uncertain"), row("low", "low"), row("conflict", condition="conflict"), row("expired", deadline=(now-timedelta(days=1)).isoformat())]
    monkeypatch.setattr("enterprises.views.match_policies", lambda *args: {"items": deepcopy(rows)})
    response = client.get(f"/api/v1/enterprises/{profile.pk}/home")
    assert response.status_code == 200
    assert [r["policy_id"] for r in response.data["uncertain"]] == ["unknown"]
    assert [r["policy_id"] for r in response.data["deadlines"]] == ["good"]
    assert response.data["counts"] == {"recommended": 2, "uncertain": 1}
    assert "retrieval" not in response.data["recommended"][0]


def test_subscription_preview_is_readonly_and_confirmation_requires_current_revision(customer):
    _, profile, client = customer
    url = f"/api/v1/enterprises/{profile.pk}/subscription-plan"
    response = client.get(url)
    assert response.status_code == 200 and response.data["rules"]
    assert not Subscription.objects.exists()
    assert client.post(url, {"enabled": True, "revision": 2}, format="json").status_code == 409
    assert not Subscription.objects.exists()
    response = client.post(url, {"enabled": True, "revision": 1}, format="json")
    assert response.status_code == 200, response.data
    assert Subscription.objects.count() == 1
    assert client.post(url, {"enabled": True, "revision": 1}, format="json").status_code == 200
    assert Subscription.objects.count() == 1
    assert client.post(url, {"enabled": False, "revision": 1}, format="json").status_code == 200
    assert Subscription.objects.get().active
    other = EnterpriseProfile.objects.create(organization=Organization.objects.create(name="other"))
    project = EnterpriseProject.objects.create(profile=other, name="private")
    assert client.get(url, {"project_id": str(project.pk)}).status_code == 404


def test_policy_customer_contract_hides_internal_fields_even_for_staff(customer):
    user, _, client = customer
    policy = Policy.objects.create(title="政策", body="政策正文", publication_date=timezone.now().date(), source_key="layout", content_hash="layout", status="published", source_grade="L1")
    for staff in (False, True):
        user.is_staff = staff
        user.save(update_fields=["is_staff"])
        response = client.get(f"/api/v1/policies/{policy.pk}")
        assert response.status_code == 200
        assert response.data["body"] == "政策正文"
        assert not {"pipeline", "ai_enrichment", "extraction_version", "scope_evidence"} & response.data.keys()
    assert set(client.get("/api/v1/overview").data) == {"policies", "subscriptions", "unread"}
