from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from accounts.models import Membership, Organization, User
from core.models import AuditRecord
from django.utils import timezone
from enterprises import watching
from enterprises.models import (
    EnterpriseProfile,
    EnterpriseProject,
    PolicyRecommendation,
    PolicyWatch,
    WatchRun,
)
from policies.models import Policy, PublicationEvent
from rest_framework.test import APIClient
from subscriptions.delivery import deliver_digests, visible_notifications
from subscriptions.models import Notification, NotificationPreference, PendingDelivery

pytestmark = pytest.mark.django_db


@pytest.fixture
def fixture(settings):
    settings.AI_BASE_URL = "http://localhost:11434/v1"
    settings.AI_MODEL = "test"
    user = User.objects.create_user("continuous")
    org = Organization.objects.create(name="合成水务企业")
    Membership.objects.create(user=user, organization=org, role="admin")
    profile = EnterpriseProfile.objects.create(
        organization=org, confirmed_at=timezone.now(), data={"business_domains": ["urban_sewage"]}
    )
    watch = PolicyWatch.objects.create(
        user=user, profile=profile, enabled=True, view="policies", consented_at=timezone.now()
    )
    config = watching.configuration()
    client = APIClient()
    client.force_authenticate(user)
    return SimpleNamespace(user=user, profile=profile, watch=watch, config=config, client=client)


def policy(key="one"):
    p = Policy.objects.create(
        title="城镇污水处理运行办法",
        body="支持城镇污水处理设施数字化建设。运营企业应当建立设备巡检和维护制度。",
        source_key=key,
        content_hash=key,
        source_url="https://example.gov.cn/" + key,
        publication_date=date(2026, 1, 1),
        status="published",
        source_grade="L1",
        validity_status="effective",
        business_domains=["urban_sewage"],
        geographic_level="national",
    )
    PublicationEvent.objects.create(
        policy=p, policy_version=p.version, kind="policy.published.v1", payload={}
    )
    return p


def run(f, full=True, notify=False):
    f.watch.refresh_from_db()
    f.watch.profile.refresh_from_db()
    return WatchRun.objects.create(
        watch=f.watch,
        signature=watching.run_signature(f.watch, f.config),
        cutoff=timezone.now(),
        full_scan=full,
        notify_changes=notify,
    )


def test_baseline_then_new_policy_reuses_daily_digest_and_deduplicates(fixture):
    f = fixture
    policy()
    watching.process_batch(run(f).pk)
    assert PolicyRecommendation.objects.count() == 1
    assert not PendingDelivery.objects.exists()
    p = policy("new")
    second = run(f, full=False, notify=True)
    watching.process_batch(second.pk)
    assert PendingDelivery.objects.count() == 1
    watching.process_batch(second.pk)
    watching.process_batch(run(f, notify=True).pk)
    assert PendingDelivery.objects.count() == 1
    NotificationPreference.objects.update_or_create(user=f.user, defaults={"digest_hour": 0})
    assert deliver_digests(timezone.now() + timedelta(days=1)) == 1
    n = Notification.objects.get()
    assert n.kind == "digest"
    response = f.client.get("/api/v1/notifications")
    assert str(p.pk) == response.data["items"][0]["items"][0]["policy_id"]
    assert deliver_digests(timezone.now() + timedelta(days=1)) == 0


@pytest.mark.parametrize("change", ["stop", "membership", "withdraw"])
def test_pending_recommendation_is_not_delivered_after_access_or_policy_change(fixture, change):
    f = fixture
    p = policy()
    watching.process_batch(run(f, notify=True).pk)
    if change == "stop":
        PolicyWatch.objects.filter(pk=f.watch.pk).update(enabled=False)
    elif change == "membership":
        Membership.objects.filter(user=f.user).update(active=False)
    else:
        Policy.objects.filter(pk=p.pk).update(status="withdrawn")
    assert deliver_digests(timezone.now() + timedelta(days=1)) == 0
    assert not Notification.objects.exists()
    assert not visible_notifications(f.user).exists()


def test_policy_and_profile_revision_fence_late_ai_result(fixture, monkeypatch):
    f = fixture
    policy()
    f.watch.ai_explanations = True
    f.watch.save()

    def stale(*args, **kwargs):
        EnterpriseProfile.objects.filter(pk=f.profile.pk).update(revision=2)
        return {"points": [], "notice": "合成说明", "conditions": {"status": "unknown"}}

    monkeypatch.setattr(watching, "explain_match", stale)
    task = run(f)
    watching.process_batch(task.pk)
    task.refresh_from_db()
    assert task.status == "cancelled"
    assert not PolicyRecommendation.objects.exists()


def test_budget_keeps_rule_result_without_model_request(fixture, monkeypatch):
    f = fixture
    policy()
    f.watch.ai_explanations = True
    f.watch.save()
    f.config.daily_model_calls = 0
    f.config.save()
    monkeypatch.setattr(watching, "explain_match", lambda *a, **kw: pytest.fail("no model budget"))
    watching.process_batch(run(f).pk)
    item = PolicyRecommendation.objects.get()
    assert "额度" in item.result["analysis_message"]
    assert not AuditRecord.objects.filter(action="watch.model_reserved").exists()


def test_batch_checkpoint_and_expired_lease_resume_without_duplicate(fixture):
    f = fixture
    policy()
    policy("two")
    policy("three")
    f.config.batch_size = 1
    f.config.save()
    task = run(f)
    watching.process_batch(task.pk)
    task.refresh_from_db()
    assert task.scanned == 1 and task.status == "queued"
    WatchRun.objects.filter(pk=task.pk).update(
        status="running", lease_until=timezone.now() - timedelta(minutes=1)
    )
    for _ in range(3):
        watching.process_batch(task.pk)
    task.refresh_from_db()
    assert task.status == "completed" and task.scanned == 3 and task.matched == 3
    assert PolicyRecommendation.objects.count() == 3


def test_daily_batch_budget_defers_without_losing_position(fixture):
    f = fixture
    policy()
    f.config.daily_batches = 1
    f.config.save()
    AuditRecord.objects.create(action="watch.batch_started", object_id=f.watch.pk)
    task = run(f)
    watching.process_batch(task.pk)
    task.refresh_from_db()
    assert task.status == "queued" and task.scanned == 0 and task.retry_at > timezone.now()


def test_customer_may_only_configure_own_scope_and_feedback(fixture):
    f = fixture
    policy()
    other = User.objects.create_user("other")
    other_client = APIClient()
    other_client.force_authenticate(other)
    values = {"profile": str(f.profile.pk), "enabled": True, "view": "policies"}
    assert (
        other_client.post("/api/v1/policy-watches/configure", values, format="json").status_code
        == 404
    )
    assert f.client.get("/api/v1/admin/recommendation-settings").status_code == 403
    task = run(f)
    watching.process_batch(task.pk)
    item = PolicyRecommendation.objects.get()
    assert (
        other_client.post(
            f"/api/v1/policy-watches/feedback/{item.pk}", {"feedback": "useful"}, format="json"
        ).status_code
        == 404
    )
    assert (
        f.client.post(
            f"/api/v1/policy-watches/feedback/{item.pk}", {"feedback": "useful"}, format="json"
        ).status_code
        == 200
    )
    values["enabled"] = False
    assert (
        f.client.post("/api/v1/policy-watches/configure", values, format="json").status_code == 200
    )
    f.watch.refresh_from_db()
    assert not f.watch.enabled and f.watch.consent_version == 2


def test_project_scope_cannot_be_taken_from_another_enterprise(fixture):
    f = fixture
    other_profile = EnterpriseProfile.objects.create(
        organization=Organization.objects.create(name="其他企业")
    )
    project = EnterpriseProject.objects.create(profile=other_profile, name="其他项目")
    assert (
        f.client.post(
            "/api/v1/policy-watches/configure",
            {"profile": str(f.profile.pk), "project": str(project.pk), "enabled": True},
            format="json",
        ).status_code
        == 404
    )


def test_scheduler_aggregates_changes_and_does_not_create_duplicate_runs(fixture):
    f = fixture
    assert watching.start_due_runs() == 0
    old = timezone.now() - timedelta(minutes=20)
    PolicyWatch.objects.filter(pk=f.watch.pk).update(updated_at=old)
    EnterpriseProfile.objects.filter(pk=f.profile.pk).update(updated_at=old)
    assert watching.start_due_runs() == 1
    assert watching.start_due_runs() == 0


def test_retention_keeps_policy_profile_and_feedback(fixture):
    f = fixture
    p = policy()
    task = run(f)
    watching.process_batch(task.pk)
    item = PolicyRecommendation.objects.get()
    old = timezone.now() - timedelta(days=100)
    WatchRun.objects.filter(pk=task.pk).update(created_at=old)
    PolicyRecommendation.objects.filter(pk=item.pk).update(
        created_at=old, active=False, feedback="useful"
    )
    watching.prune_history()
    assert not WatchRun.objects.filter(pk=task.pk).exists()
    item.refresh_from_db()
    assert item.result and item.feedback == "useful"
    assert (
        Policy.objects.filter(pk=p.pk).exists()
        and EnterpriseProfile.objects.filter(pk=f.profile.pk).exists()
    )


def test_instant_notifications_do_not_repeat_on_unchanged_recheck(fixture):
    f = fixture
    policy()
    NotificationPreference.objects.create(user=f.user, update_mode="instant")
    watching.process_batch(run(f, notify=True).pk)
    watching.process_batch(run(f, notify=True).pk)
    assert Notification.objects.count() == 1 and not PendingDelivery.objects.exists()


def test_expired_previously_relevant_policy_sends_one_change_warning(fixture):
    f = fixture
    p = policy()
    watching.process_batch(run(f).pk)
    p.validity_status = "expired"
    p.save()
    watching.process_batch(run(f, notify=True).pk)
    notice = Notification.objects.get()
    assert notice.recommendation.result["change_only"]
    assert "变化" in notice.title
    assert visible_notifications(f.user).count() == 1
    watching.process_batch(run(f, notify=True).pk)
    assert Notification.objects.count() == 1


def test_cached_ai_uncertainty_is_preserved_on_recheck(fixture, monkeypatch):
    f = fixture
    policy()
    f.watch.ai_explanations = True
    f.watch.save()
    monkeypatch.setattr(
        watching,
        "explain_match",
        lambda *a, **kw: {
            "points": [],
            "notice": "有新条款待核实",
            "conditions": {"status": "unknown"},
        },
    )
    watching.process_batch(run(f).pk)
    watching.process_batch(run(f).pk)
    item = PolicyRecommendation.objects.get()
    assert item.result["recommendation_group"] == "needs_verification"
    assert item.result["analysis"]["conditions"]["status"] == "unknown"
