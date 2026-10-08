from unittest.mock import patch

import pytest
from accounts.models import Entitlement, User
from enterprises.models import EnterpriseProfile, ResearchRun
from enterprises.tasks import process_research
from policies.models import PublicationEvent
from rest_framework.test import APIClient
from subscriptions.models import PendingDelivery, Subscription
from subscriptions.services import deliver_event
from test_enterprise_profiles import policy, profile_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def customer():
    user = User.objects.create_user(username="onboarding-customer")
    client = APIClient()
    client.force_authenticate(user)
    return user, client


def test_registration_does_not_require_or_create_enterprise_and_subscription():
    client = APIClient()
    result = client.post("/api/v1/auth/register", {
        "username": "new-onboarding-user", "password": "Test-only-Complex-9287!",
        "password_confirm": "Test-only-Complex-9287!",
    }, format="json")
    assert result.status_code == 201
    assert client.get("/api/v1/enterprises").status_code == 200
    assert not EnterpriseProfile.objects.exists()
    assert not Subscription.objects.exists()


def test_generated_draft_waits_for_confirmation_then_subscribes_and_notifies(customer):
    user, client = customer
    with patch("enterprises.views.get_ai_profile") as ai, patch("enterprises.views.enqueue"):
        ai.return_value.configured = True
        result = client.post("/api/v1/enterprise-research", {
            "name": "测试水务有限公司", "source_mode": "text",
            "introduction": "测试水务有限公司主营城镇污水处理，提供水务运营与相关技术服务。",
        }, format="json")
    assert result.status_code == 202
    run = ResearchRun.objects.get(pk=result.data["id"])
    draft = {"candidates": [{"name": "测试水务有限公司", "data": {
        "business_domains": ["urban_sewage"], "city": "南宁",
    }, "evidence": {}}]}
    with patch("enterprises.tasks.company_graph", return_value=draft):
        process_research(str(run.pk))
    assert not EnterpriseProfile.objects.exists() and not Subscription.objects.exists()
    saved = client.post("/api/v1/enterprises", {
        **draft["candidates"][0], "run_id": str(run.pk), "auto_subscribe": True,
    }, format="json")
    assert saved.status_code == 201
    assert saved.data["subscription_result"]["created_count"] == 1
    sub = Subscription.objects.get(user=user)
    assert sub.business_domain == "urban_sewage" and sub.target_view == "all"
    assert not sub.city and not sub.province  # National policies must not be excluded.
    national = policy()
    event = PublicationEvent.objects.create(policy=national, policy_version=national.version,
                                            kind="policy.published", payload={"subscription_ids": [str(sub.pk)]})
    assert deliver_event(event.pk) == 1
    assert deliver_event(event.pk) == 0
    assert PendingDelivery.objects.filter(user=user, handled_at__isnull=True).count() == 1


def test_opt_out_and_empty_tags_never_create_broad_subscription(customer):
    _, client = customer
    first = client.post("/api/v1/enterprises", {"name": "不订阅企业", "data": {
        "business_domains": ["urban_sewage"],
    }}, format="json")
    assert first.status_code == 201 and first.data["subscription_result"] is None
    second = client.post("/api/v1/enterprises", {"name": "缺少领域企业", "data": {}, "auto_subscribe": True}, format="json")
    assert second.status_code == 201
    assert second.data["subscription_result"]["status"] == "unavailable"
    assert not Subscription.objects.exists()


@pytest.mark.parametrize("allowed,limit", [(False, None), (True, 0)])
def test_permission_or_quota_failure_keeps_profile_but_no_subscription(customer, allowed, limit):
    user, client = customer
    Entitlement.objects.create(user=user, capability="policy_subscription", allowed=allowed, limit=limit)
    response = client.post("/api/v1/enterprises", {"name": "受限企业", "data": {
        "business_domains": ["urban_sewage"],
    }, "auto_subscribe": True}, format="json")
    assert response.status_code == 201
    assert response.data["subscription_result"]["status"] == "unavailable"
    assert EnterpriseProfile.objects.count() == 1 and not Subscription.objects.exists()


def test_duplicate_confirmation_and_other_enterprise_do_not_duplicate_rules(customer):
    user, client = customer
    body = {"name": "企业甲", "data": {"business_domains": ["urban_sewage", "urban_sewage"]}, "auto_subscribe": True}
    first = client.post("/api/v1/enterprises", body, format="json")
    assert first.status_code == 201
    updated = client.patch(f"/api/v1/enterprises/{first.data['id']}", body | {"revision": 1}, format="json")
    assert updated.status_code == 200
    assert updated.data["subscription_result"]["status"] == "unchanged"
    other = client.post("/api/v1/enterprises", body | {"name": "企业乙"}, format="json")
    assert other.status_code == 201 and Subscription.objects.filter(user=user).count() == 1


def test_paused_or_edited_rule_is_not_reenabled_or_overwritten(customer):
    user, client = customer
    body = {"name": "企业甲", "data": {"business_domains": ["urban_sewage"]}, "auto_subscribe": True}
    first = client.post("/api/v1/enterprises", body, format="json")
    Subscription.objects.filter(user=user).update(active=False, keywords="人工限定")
    result = client.patch(f"/api/v1/enterprises/{first.data['id']}", body | {"revision": 1}, format="json")
    assert result.status_code == 200
    assert "暂停或修改" in result.data["subscription_result"]["message"]
    sub = Subscription.objects.get(user=user)
    assert not sub.active and sub.keywords == "人工限定"


def test_existing_equivalent_manual_rule_is_reused(customer):
    user, client = customer
    Subscription.objects.create(user=user, name="原有订阅", business_domain="urban_sewage",
                                target_view="all", idempotency_key="manual")
    response = client.post("/api/v1/enterprises", {"name": "企业甲", "data": {
        "business_domains": ["urban_sewage"],
    }, "auto_subscribe": True}, format="json")
    assert response.data["subscription_result"]["created_count"] == 0
    assert Subscription.objects.count() == 1


def test_other_users_profile_cannot_be_used_to_create_subscriptions(customer):
    user, client = customer
    other = User.objects.create_user(username="another-onboarding-user")
    profile = profile_for(other, business_domains=["urban_sewage"])
    response = client.patch(f"/api/v1/enterprises/{profile.pk}", {
        "name": profile.organization.name, "revision": 1,
        "data": profile.data, "auto_subscribe": True,
    }, format="json")
    assert response.status_code == 404
    assert not Subscription.objects.filter(user=user).exists()
