from datetime import timedelta
from unittest.mock import patch

import pytest
from accounts.models import Membership, Organization, User
from core.models import AuditRecord
from django.utils import timezone
from enterprises import watching
from enterprises.models import (
    EnterpriseProfile,
    PolicyWatch,
    ResearchRun,
    ResearchSettings,
    ScopedSettings,
    WatchRun,
)
from enterprises.runtime import input_issue
from enterprises.scoped_settings import recommendation_configuration, research_configuration
from enterprises.tasks import schedule_refreshes
from enterprises.workflow import start
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(settings):
    settings.AI_BASE_URL = "http://localhost:11434/v1"
    settings.AI_MODEL = "test"
    admin = User.objects.create_user("scoped-admin", is_staff=True, is_superuser=True)
    owner = User.objects.create_user("scoped-owner")
    other = User.objects.create_user("scoped-other")
    profiles = []
    for user in (owner, other):
        org = Organization.objects.create(name=user.username)
        Membership.objects.create(user=user, organization=org, role="admin")
        profiles.append(EnterpriseProfile.objects.create(organization=org, data={"business_domains": ["water_supply"], "website": "https://example.com"}, confirmed_at=timezone.now()))
    ResearchSettings.objects.create(daily_limit=1)
    client = APIClient()
    client.force_authenticate(admin)
    return admin, owner, other, profiles, client


def test_admin_can_save_scoped_values_audit_and_reset_with_conflict_control(setup):
    admin, owner, other, _, client = setup
    url = f"/api/v1/admin/user-settings/{owner.pk}"
    initial = client.get(url)
    assert initial.status_code == 200 and initial.data["revision"] == 0
    assert "api_key" not in initial.data["defaults"]
    response = client.patch(url, {"revision": 0, "overrides": {"daily_limit": 7, "active_limit": 3}}, format="json")
    assert response.status_code == 200 and response.data["effective"]["daily_limit"] == 7
    assert research_configuration(other).daily_limit == 1
    assert AuditRecord.objects.filter(actor=admin, action="enterprise.scoped_settings.updated").exists()
    assert client.patch(url, {"revision": 0, "overrides": {}}, format="json").status_code == 409
    reset = client.patch(url, {"revision": 1, "overrides": {}}, format="json")
    assert reset.status_code == 200 and reset.data["effective"]["daily_limit"] == 1


@pytest.mark.parametrize("staff", [False, True])
def test_non_manager_cannot_read_or_change_scoped_settings(setup, staff):
    _, owner, _, profiles, client = setup
    owner.is_staff = staff
    owner.save()
    client.force_authenticate(owner)
    for url in (f"/api/v1/admin/user-settings/{owner.pk}", f"/api/v1/admin/enterprise-settings/{profiles[0].pk}"):
        assert client.get(url).status_code == 403
        assert client.patch(url, {"revision": 0, "overrides": {}}, format="json").status_code == 403


@pytest.mark.parametrize("overrides", [{"api_key": "forbidden"}, {"daily_limit": 0}, {"active_limit": 8}, {"agent_enabled": True}])
def test_user_config_rejects_foreign_fields_and_invalid_bounds(setup, overrides):
    _, owner, _, _, client = setup
    response = client.patch(f"/api/v1/admin/user-settings/{owner.pk}", {"revision": 0, "overrides": overrides}, format="json")
    assert response.status_code == 400 and not ScopedSettings.objects.exists()


def test_entity_values_isolated_and_null_inherits_changed_default(setup):
    _, owner, other, profiles, client = setup
    response = client.patch(f"/api/v1/admin/enterprise-settings/{profiles[0].pk}", {"revision": 0, "overrides": {"workflow_max_policies": 1, "workflow_cache_hours": 0, "agent_enabled": True, "max_sources": None}}, format="json")
    assert response.status_code == 200
    assert research_configuration(owner, profiles[0]).workflow_max_policies == 1
    assert research_configuration(other, profiles[1]).workflow_max_policies == 3
    assert research_configuration(owner, profiles[0]).agent_all_organizations is True
    settings = ResearchSettings.objects.get()
    settings.max_sources = 8
    settings.save()
    assert research_configuration(owner, profiles[0]).max_sources == 8
    assert "max_sources" not in ScopedSettings.objects.get().overrides


def test_creation_honors_account_limit_without_changing_other_account(setup):
    _, owner, other, _, client = setup
    ScopedSettings.objects.create(user=owner, overrides={"daily_limit": 3, "active_limit": 3})
    for user in (owner, other):
        ResearchRun.objects.create(user=user, fingerprint="past", status="completed")
    values = {"name": "测试水务有限公司", "source_mode": "text", "introduction": "公司从事供水和污水处理运营，提供设施设计建设和运行维护服务。"}
    with patch("enterprises.views.enqueue"):
        client.force_authenticate(owner)
        assert client.post("/api/v1/enterprise-research", values, format="json").status_code == 202
        client.force_authenticate(other)
        assert client.post("/api/v1/enterprise-research", values, format="json").status_code == 400


def test_account_concurrency_and_enterprise_feature_switch_enforced(setup):
    _, owner, _, profiles, client = setup
    ScopedSettings.objects.create(user=owner, overrides={"daily_limit": 5, "active_limit": 1})
    ResearchRun.objects.create(user=owner, fingerprint="active", status="queued")
    client.force_authenticate(owner)
    values = {"profile_id": str(profiles[0].pk), "name": owner.username, "source_mode": "text", "introduction": "公司从事供水和污水处理运营，提供设施设计建设和运行维护服务。"}
    with patch("enterprises.views.enqueue"):
        assert client.post("/api/v1/enterprise-research", values, format="json").status_code == 400
    ScopedSettings.objects.create(profile=profiles[0], overrides={"research_allowed": False})
    ResearchRun.objects.filter(user=owner).update(status="failed")
    response = client.post("/api/v1/enterprise-research", values, format="json")
    assert response.status_code == 400 and "暂未开放" in response.data["message"]


def test_workflow_snapshot_uses_only_target_config_and_detects_its_changes(setup):
    _, owner, _, profiles, _ = setup
    own = ScopedSettings.objects.create(profile=profiles[0], overrides={"workflow_max_policies": 1, "workflow_concurrency": 2})
    with patch("enterprises.tasks.enqueue"):
        root = start(owner, profiles[0])
    assert root.runtime_snapshot["max_policies"] == 1 and root.runtime_snapshot["concurrency"] == 2
    ScopedSettings.objects.create(profile=profiles[1], overrides={"workflow_max_policies": 7})
    assert input_issue(root, configuration=True) == ""
    own.overrides["workflow_max_policies"] = 2
    own.save()
    assert "配置已变化" in input_issue(root, configuration=True)


def test_scheduled_refresh_respects_enterprise_gate_and_owner_budget(setup):
    _, owner, _, profiles, _ = setup
    profiles[0].research_method, profiles[0].refresh_days = "website", 7
    profiles[0].next_research_at = timezone.now() - timedelta(minutes=1)
    profiles[0].save()
    row = ScopedSettings.objects.create(profile=profiles[0], overrides={"research_allowed": False})
    with patch("enterprises.tasks.enqueue"):
        schedule_refreshes()
    assert not ResearchRun.objects.exists()
    row.overrides = {"research_allowed": True}
    row.save()
    ScopedSettings.objects.create(user=owner, overrides={"daily_limit": 3})
    ResearchRun.objects.create(user=owner, fingerprint="past", status="completed")
    with patch("enterprises.tasks.enqueue"):
        schedule_refreshes()
    assert ResearchRun.objects.filter(profile=profiles[0], status="queued").exists()


def test_recommendation_schedule_and_retention_use_entity_override(setup):
    _, owner, other, profiles, _ = setup
    first = PolicyWatch.objects.create(user=owner, profile=profiles[0], enabled=True, consented_at=timezone.now())
    second = PolicyWatch.objects.create(user=other, profile=profiles[1], enabled=True, consented_at=timezone.now())
    ScopedSettings.objects.create(profile=profiles[0], overrides={"recommendation_enabled": False, "recommendation_retention_days": 7})
    ScopedSettings.objects.create(profile=profiles[1], overrides={"recommendation_aggregation_minutes": 1, "recommendation_retention_days": 30})
    PolicyWatch.objects.all().update(updated_at=timezone.now() - timedelta(hours=1))
    EnterpriseProfile.objects.all().update(updated_at=timezone.now() - timedelta(hours=1))
    assert watching.start_due_runs() == 1
    assert not first.runs.exists() and second.runs.exists()
    old = []
    for watch in (first, second):
        row = WatchRun.objects.create(watch=watch, status="completed", signature="past", cutoff=timezone.now())
        WatchRun.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=10))
        old.append(row)
    watching.prune_history()
    assert not WatchRun.objects.filter(pk=old[0].pk).exists()
    assert WatchRun.objects.filter(pk=old[1].pk).exists()


def test_recommendation_per_entity_budget_does_not_count_other_entity(setup):
    _, owner, other, profiles, _ = setup
    platform = watching.configuration()
    platform.daily_model_calls = 50
    platform.save()
    ScopedSettings.objects.create(profile=profiles[0], overrides={"recommendation_daily_model_calls": 1})
    watches = [PolicyWatch.objects.create(user=user, profile=profile, enabled=True) for user, profile in zip((owner, other), profiles)]
    roots = [WatchRun.objects.create(watch=watch, status="running", signature="test", cutoff=timezone.now()) for watch in watches]
    AuditRecord.objects.create(action="watch.model_reserved", object_id=roots[1].pk, details={"profile_id": str(profiles[1].pk)})
    config = watching.configuration(profiles[0])
    assert watching.reserve_model_call(roots[0], config)
    assert not watching.reserve_model_call(roots[0], config)


def test_maintenance_listing_scopes_to_user_and_optional_entity(setup):
    _, owner, other, profiles, client = setup
    one = ResearchRun.objects.create(user=owner, profile=profiles[0], fingerprint="one", status="completed")
    ResearchRun.objects.create(user=owner, fingerprint="unbound", status="failed")
    ResearchRun.objects.create(user=other, profile=profiles[1], fingerprint="other", status="completed")
    url = f"/api/v1/enterprise-research/saved-results?user_id={owner.pk}&profile_id={profiles[0].pk}"
    response = client.get(url)
    assert response.status_code == 200 and response.data["count"] == 1 and response.data["items"][0]["id"] == str(one.pk)
    assert client.get(f"/api/v1/enterprise-research/saved-results?user_id={owner.pk}").data["count"] == 2


def test_disabled_research_and_matching_apply_to_supplement_and_continuous_ai(setup):
    from enterprises.agent import snapshot_for

    _, owner, _, profiles, _ = setup
    ScopedSettings.objects.create(profile=profiles[0], overrides={"agent_enabled": True, "research_allowed": False, "matching_allowed": False})
    assert snapshot_for(profiles[0], user=owner) == {}
    assert recommendation_configuration(profiles[0]).model_calls_per_run == 0
    assert recommendation_configuration(profiles[1]).model_calls_per_run != 0
