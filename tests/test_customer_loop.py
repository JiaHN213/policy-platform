from datetime import timedelta

import pytest
from accounts.models import User
from django.utils import timezone
from enterprises.regions import region_preference
from ingestion.models import DiscoveredItem, Source, SourceCheckRun
from policies.models import Opportunity, OpportunityBatch, Policy, PublicationEvent
from rest_framework.test import APIClient
from subscriptions.delivery import deadline_reminders, deliver_digests
from subscriptions.following import sync_pending_follows
from subscriptions.models import (
    Notification,
    NotificationPreference,
    PendingDelivery,
    ProfileFollow,
    Subscription,
)
from subscriptions.services import deliver_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def customer():
    user = User.objects.create_user("loop-customer")
    client = APIClient()
    client.force_authenticate(user)
    return user, client


def policy(key="loop"):
    return Policy.objects.create(title="水务政策", issuer="官方", publication_date=timezone.localdate(),
        status="published", source_grade="L1", validity_status="effective", business_domains=["urban_sewage"],
        body="水务企业可以申报，适用范围为全国。", source_key=key, content_hash=key, source_url="https://example.gov.cn/" + key)


def opportunity(policy):
    proof = {"verification_status": "verified", "evidence_policy": policy, "evidence_version": policy.version, "evidence_quote": policy.body}
    op = Opportunity.objects.create(policy=policy, title="水务支持", status="open", category="fiscal", **proof)
    batch = OpportunityBatch.objects.create(opportunity=op, name="本期", status="open", deadline_at=timezone.now() + timedelta(days=6), **proof)
    return op, batch


def subscribe(user, **kwargs):
    return Subscription.objects.create(user=user, name="关注水务", idempotency_key=str(Subscription.objects.count()), **kwargs)


def publish_event(policy, subscriptions, **kwargs):
    return PublicationEvent.objects.create(policy=policy, policy_version=policy.version,
        payload={"subscription_ids": [str(s.pk) for s in subscriptions]}, **kwargs)


def test_follow_keeps_manual_pause_and_other_company_and_can_restore(customer):
    user, client = customer
    body = {"name": "甲企业", "data": {"business_domains": ["urban_sewage"], "interest_regions": ["南宁", "柳州"]}, "follow_subscriptions": True}
    response = client.post("/api/v1/enterprises", body, format="json")
    assert response.status_code == 201 and response.data["follow_subscriptions"]
    first = Subscription.objects.get(user=user)
    assert first.managed and first.interest_regions == ["南宁", "柳州"]
    other = client.post("/api/v1/enterprises", body | {"name": "乙企业"}, format="json")
    assert other.status_code == 201
    paused = client.patch(f"/api/v1/subscriptions/{first.pk}", {"active": False, "revision": 1}, format="json")
    assert paused.status_code == 200 and not paused.data["managed"]
    updated = client.patch(f"/api/v1/enterprises/{response.data['id']}", body | {"revision": 1, "data": {"business_domains": ["water_supply"]}}, format="json")
    assert updated.status_code == 200
    first.refresh_from_db()
    assert not first.active and first.business_domain == "urban_sewage"
    assert Subscription.objects.get(source_profile_id=other.data["id"]).active
    changes = client.get(f"/api/v1/subscriptions/{first.pk}/history").data
    restored = client.post(f"/api/v1/subscriptions/{first.pk}/restore", {"change_id": str(changes["items"][0]["id"]), "revision": first.revision}, format="json")
    assert restored.status_code == 200 and restored.data["active"] and not restored.data["managed"]
    assert client.post(f"/api/v1/subscriptions/{first.pk}/restore", {"change_id": str(changes["items"][0]["id"]), "revision": first.revision}, format="json").status_code == 409


def test_deleted_follow_rule_is_not_recreated_and_history_is_private(customer):
    user, client = customer
    body = {"name": "甲企业", "data": {"business_domains": ["urban_sewage"]}, "follow_subscriptions": True}
    result = client.post("/api/v1/enterprises", body, format="json")
    sub = Subscription.objects.get(user=user)
    stranger = APIClient()
    stranger.force_authenticate(User.objects.create_user("outsider"))
    assert stranger.get(f"/api/v1/subscriptions/{sub.pk}/history").status_code == 404
    assert client.delete(f"/api/v1/subscriptions/{sub.pk}").status_code == 204
    assert client.patch(f"/api/v1/enterprises/{result.data['id']}", body | {"revision": 1}, format="json").status_code == 200
    sync_pending_follows()
    assert Subscription.objects.count() == 1 and not Subscription.objects.get().active
    assert client.get("/api/v1/subscriptions").data["count"] == 0


def test_daily_digest_deduplicates_and_uses_live_visibility(customer):
    user, client = customer
    subs = [subscribe(user), subscribe(user)]
    first, second = policy("one"), policy("two")
    for p in [first, second]:
        event = publish_event(p, subs)
        assert deliver_event(event.pk) == 1
        assert deliver_event(event.pk) == 0
    assert not Notification.objects.exists() and PendingDelivery.objects.count() == 2
    tomorrow = timezone.localtime() + timedelta(days=1)
    tomorrow = tomorrow.replace(hour=9, minute=1)
    assert deliver_digests(tomorrow) == 1
    assert deliver_digests(tomorrow) == 0
    result = client.get("/api/v1/notifications").data
    assert result["count"] == 1 and len(result["items"][0]["items"]) == 2
    first.status = "withdrawn"
    first.save()
    result = client.get("/api/v1/notifications").data
    assert len(result["items"][0]["items"]) == 1


def test_unsubscribe_before_digest_cancels_pending_delivery(customer):
    user, _ = customer
    sub = subscribe(user)
    event = publish_event(policy(), [sub])
    deliver_event(event.pk)
    sub.active = False
    sub.save()
    assert deliver_digests(timezone.localtime().replace(hour=10) + timedelta(days=1)) == 0
    assert not Notification.objects.exists()
    assert PendingDelivery.objects.get().handled_at


def test_suspended_opportunity_notifies_previous_open_subscriber_immediately(customer):
    user, _ = customer
    sub = subscribe(user, target_view="opportunity", opportunity_status="open")
    p = policy()
    op, _ = opportunity(p)
    op.status = "suspended"
    op.save()
    event = PublicationEvent.objects.get(kind__startswith="opportunity.changed.")
    assert event.payload["previous_matching"] == {str(sub.pk): sub.revision}
    assert deliver_event(event.pk) == 1
    assert Notification.objects.get().kind == "important"
    assert not PendingDelivery.objects.exists()


def test_deadline_nodes_deduplicate_across_rules_and_allow_changed_deadline(customer):
    user, _ = customer
    NotificationPreference.objects.create(user=user, deadline_enabled=True, deadline_days=[7, 1])
    subscribe(user, target_view="all")
    subscribe(user, target_view="opportunity")
    p = policy()
    _, batch = opportunity(p)
    now = timezone.now()
    assert deadline_reminders(now) == 1
    assert deadline_reminders(now) == 0
    assert deadline_reminders(now + timedelta(days=5, hours=1)) == 1
    assert Notification.objects.filter(kind="deadline").count() == 2
    batch.deadline_at += timedelta(days=1)
    batch.save()
    assert deadline_reminders(now + timedelta(days=5, hours=1)) == 1
    assert deadline_reminders(now + timedelta(days=5, hours=1)) == 0


def test_preferences_validate_and_source_failure_never_means_no_updates(customer):
    _, client = customer
    assert client.patch("/api/v1/notifications/preferences", {"digest_hour": 30}, format="json").status_code == 400
    assert client.patch("/api/v1/notifications/preferences", {"deadline_days": [7, 1, 7], "deadline_enabled": True}, format="json").data["deadline_days"] == [7, 1]
    source = Source.objects.create(name="官方库", url="https://example.gov.cn", enabled=True, verification_status="verified")
    SourceCheckRun.objects.create(source=source, status="failed", finished_at=timezone.now())
    item = DiscoveredItem.objects.create(source=source, url="https://example.gov.cn/one", title="待处理")
    DiscoveredItem.objects.filter(pk=item.pk).update(created_at=timezone.now() - timedelta(days=2))
    assert client.get("/api/v1/source-coverage").status_code == 403
    user = customer[0]
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    data = client.get("/api/v1/source-coverage").data["items"][0]
    assert "失败" in data["state"] and data["overdue_24h_links"] == 1


def test_region_preference_uses_scope_quote_not_issuer(customer):
    p = policy()
    p.geographic_level = "national"
    op, _ = opportunity(p)
    assert not region_preference(p, ["南宁"], [op])["matched"]
    op.evidence_details = [{"field": "regions", "quote": "适用范围为全国"}]
    assert region_preference(p, ["南宁", "柳州"], [op])["matched"]


def test_follow_is_opt_in(customer):
    user, client = customer
    assert client.post("/api/v1/enterprises", {"name": "跳过订阅", "data": {}}, format="json").status_code == 201
    assert not Subscription.objects.filter(user=user).exists()
    assert not ProfileFollow.objects.filter(user=user, enabled=True).exists()


def test_lost_enterprise_access_stops_unchanged_profile_follow(customer):
    from accounts.models import Membership

    user, client = customer
    client.post("/api/v1/enterprises", {"name": "权限企业", "data": {"business_domains": ["urban_sewage"]}, "follow_subscriptions": True}, format="json")
    Membership.objects.filter(user=user).update(active=False)
    sync_pending_follows()
    assert not Subscription.objects.get(user=user).active
    assert not ProfileFollow.objects.get(user=user).enabled


def test_manual_validity_change_notifies_previously_matching_rule(customer):
    from policies.services import update_validity

    user, _ = customer
    admin = User.objects.create_superuser("validity-admin")
    subscribe(user, validity_status="effective")
    p = policy()
    update_validity(p.pk, admin, p.version, "repealed", p.body)
    event = PublicationEvent.objects.get(kind="policy.validity_changed.v1")
    assert deliver_event(event.pk) == 1
    assert Notification.objects.get().kind == "important"


def test_notification_dispatch_uses_dedicated_task(customer, monkeypatch):
    from policies.event_tasks import dispatch_publication_consumers

    user, _ = customer
    publish_event(policy(), [subscribe(user)])
    calls = []
    monkeypatch.setattr("policies.event_tasks.consume_notification.delay", lambda value: calls.append(value))
    from django.test import override_settings
    with override_settings(LOCAL_WORKER=False):
        dispatch_publication_consumers(consumer="subscription")
    assert len(calls) == 1
