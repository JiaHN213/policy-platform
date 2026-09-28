from datetime import date

import pytest
from accounts.models import Entitlement
from core.errors import Conflict
from django.contrib.auth import get_user_model
from policies.models import Policy, PublicationEvent
from policies.services import fingerprint, publish_policy, withdraw_policy
from rest_framework.test import APIClient
from subscriptions.models import Notification, Subscription
from subscriptions.services import deliver_event


@pytest.fixture
def users(db):
    model = get_user_model()
    return model.objects.create_superuser(
        "reviewer", password="test-pass"
    ), model.objects.create_user("reader", password="test-pass")


@pytest.fixture
def policy(db):
    return Policy.objects.create(
        title="测试供水数字化",
        document_type="policy",
        source_grade="L1",
        geographic_level="national",
        issuer="测试",
        publication_date=date(2026, 1, 1),
        body="供水与水资源保护。",
        topics=["水务"],
        source_url="https://www.gov.cn/zhengce/test.htm",
        source_key=fingerprint("test"),
        content_hash=fingerprint("供水与水资源保护。"),
    )


@pytest.mark.django_db
def test_publication_and_outbox_are_idempotent(users, policy):
    admin, reader = users
    for name in ["水务关注", "供水关注"]:
        Subscription.objects.create(user=reader, name=name, topic="水务", idempotency_key=name)
    publish_policy(policy.id, admin, 1)
    publish_policy(policy.id, admin, 1)
    assert PublicationEvent.objects.count() == 1
    event = PublicationEvent.objects.get()
    assert deliver_event(event.id) == 1
    assert deliver_event(event.id) == 0
    reasons = Notification.objects.get().reasons
    assert all("关注主题：水务" in reason for reason in reasons)
    assert any("供水关注" in reason for reason in reasons)
    assert any("水务关注" in reason for reason in reasons)


@pytest.mark.django_db
def test_new_subscription_does_not_receive_old_event(users, policy):
    admin, reader = users
    publish_policy(policy.id, admin, 1)
    Subscription.objects.create(user=reader, name="新订阅", idempotency_key="new")
    assert deliver_event(PublicationEvent.objects.get().id) == 0


@pytest.mark.django_db
def test_cancel_and_entitlement_rechecked_before_delivery(users, policy):
    admin, reader = users
    Subscription.objects.create(user=reader, name="水务", topic="水务", idempotency_key="water")
    publish_policy(policy.id, admin, 1)
    Entitlement.objects.create(user=reader, capability="policy_subscription", allowed=False)
    assert deliver_event(PublicationEvent.objects.get().id) == 0


@pytest.mark.django_db
def test_unpublished_and_withdrawn_are_hidden(users, policy):
    admin, reader = users
    client = APIClient()
    client.force_authenticate(reader)
    assert client.get(f"/api/v1/policies/{policy.id}").status_code == 404
    publish_policy(policy.id, admin, 1)
    assert client.get(f"/api/v1/policies/{policy.id}").status_code == 200
    withdraw_policy(policy.id, admin, 1)
    assert client.get(f"/api/v1/policies/{policy.id}").status_code == 404


@pytest.mark.django_db
def test_review_permission_and_version(users, policy):
    admin, reader = users
    client = APIClient()
    client.force_authenticate(reader)
    assert (
        client.post(f"/api/v1/admin/policies/{policy.id}/publish", {"version": 1}).status_code
        == 403
    )
    with pytest.raises(Conflict):
        publish_policy(policy.id, admin, 99)
    assert PublicationEvent.objects.count() == 0


@pytest.mark.django_db
def test_search_entitlement_is_enforced_separately(users, policy):
    admin, reader = users
    publish_policy(policy.id, admin, 1)
    Entitlement.objects.create(user=reader, capability="policy_search", allowed=False)
    client = APIClient()
    client.force_authenticate(reader)
    assert client.get("/api/v1/policies?q=供水").status_code == 403
    assert client.get(f"/api/v1/policies/{policy.id}").status_code == 200


@pytest.mark.django_db
def test_subscription_isolation_idempotency_and_conflict(users):
    admin, reader = users
    client = APIClient()
    client.force_authenticate(reader)
    payload = {"name": "水务订阅", "topic": "水务"}
    first = client.post("/api/v1/subscriptions", payload, HTTP_IDEMPOTENCY_KEY="key")
    assert first.status_code == 201
    assert (
        client.post("/api/v1/subscriptions", payload, HTTP_IDEMPOTENCY_KEY="key").status_code == 200
    )
    assert (
        client.post(
            "/api/v1/subscriptions", {"name": "changed"}, HTTP_IDEMPOTENCY_KEY="key"
        ).status_code
        == 409
    )
    client.force_authenticate(admin)
    assert client.get("/api/v1/subscriptions").json()["count"] == 0
    assert client.delete(f"/api/v1/subscriptions/{first.json()['id']}").status_code == 404


@pytest.mark.django_db
def test_login_requires_csrf_and_logout_clears_session(users):
    client = APIClient(enforce_csrf_checks=True)
    payload = {"username": "reader", "password": "test-pass"}
    assert client.post("/api/v1/auth/login", payload).status_code == 403
    token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    assert client.post("/api/v1/auth/login", payload, HTTP_X_CSRFTOKEN=token).status_code == 200
    assert client.get("/api/v1/me").status_code == 200
    token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    created = client.post(
        "/api/v1/subscriptions",
        {"name": "真实会话订阅", "topic": "水务"},
        HTTP_X_CSRFTOKEN=token,
        HTTP_IDEMPOTENCY_KEY="session-test",
    )
    assert created.status_code == 201
    assert client.post("/api/v1/auth/logout", HTTP_X_CSRFTOKEN=token).status_code == 200
    assert client.get("/api/v1/me").status_code == 403


@pytest.mark.django_db
def test_notification_filters_and_read_all_are_private_and_idempotent(users, policy):
    admin, reader = users
    publish_policy(policy.id, admin, 1)
    event = PublicationEvent.objects.get()
    own = Notification.objects.create(user=reader, event=event, title=policy.title)
    other = Notification.objects.create(user=admin, event=event, title=policy.title)
    client = APIClient()
    client.force_authenticate(reader)
    assert client.get("/api/v1/notifications?status=unread").json()["count"] == 1
    assert client.get("/api/v1/notifications?status=read").json()["count"] == 0
    assert client.get("/api/v1/notifications?status=invalid").status_code == 400
    assert client.post("/api/v1/notifications/read-all").json() == {"updated": 1}
    own.refresh_from_db()
    first_read = own.read_at
    assert first_read is not None
    assert client.post("/api/v1/notifications/read-all").json() == {"updated": 0}
    own.refresh_from_db()
    other.refresh_from_db()
    assert own.read_at == first_read
    assert other.read_at is None
    assert client.get("/api/v1/notifications?status=unread").json()["count"] == 0
    assert client.get("/api/v1/notifications?status=read").json()["count"] == 1


@pytest.mark.django_db
def test_read_all_does_not_touch_withdrawn_or_restricted_notifications(users, policy):
    admin, reader = users
    publish_policy(policy.id, admin, 1)
    notification = Notification.objects.create(
        user=reader, event=PublicationEvent.objects.get(), title=policy.title
    )
    client = APIClient()
    client.force_authenticate(reader)
    entitlement = Entitlement.objects.create(user=reader, capability="policy_detail", allowed=False)
    assert client.post("/api/v1/notifications/read-all").json() == {"updated": 0}
    entitlement.delete()
    withdraw_policy(policy.id, admin, 1)
    assert client.post("/api/v1/notifications/read-all").json() == {"updated": 0}
    notification.refresh_from_db()
    assert notification.read_at is None


@pytest.mark.django_db
def test_subscription_edit_and_delete_keep_existing_notifications(users, policy):
    admin, reader = users
    sub = Subscription.objects.create(user=reader, name="原关注", idempotency_key="edit")
    publish_policy(policy.id, admin, 1)
    deliver_event(PublicationEvent.objects.get().id)
    client = APIClient()
    client.force_authenticate(admin)
    assert client.patch(f"/api/v1/subscriptions/{sub.id}", {"name": "越权"}).status_code == 404
    client.force_authenticate(reader)
    result = client.patch(f"/api/v1/subscriptions/{sub.id}", {"name": "新关注", "topic": "环保"})
    assert result.status_code == 200
    assert result.json()["topic"] == "环保"
    assert client.delete(f"/api/v1/subscriptions/{sub.id}").status_code == 204
    assert Notification.objects.filter(user=reader).count() == 1
