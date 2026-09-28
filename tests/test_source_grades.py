from datetime import date

import pytest
from analysis.runtime import authorize
from django.contrib.auth import get_user_model
from policies.models import Evidence, Policy, PublicationEvent
from policies.services import fingerprint, mark_as_lead, publish_policy
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient
from subscriptions.models import Notification, Subscription
from subscriptions.services import deliver_event


@pytest.fixture
def record(db):
    admin = get_user_model().objects.create_superuser("reviewer")
    customer = get_user_model().objects.create_user("customer")
    policy = Policy.objects.create(
        title="文件",
        issuer="机关",
        publication_date=date(2026, 9, 16),
        body="测试正文",
        source_url="https://www.gov.cn/zhengce/test.htm",
        source_key=fingerprint("grades"),
        content_hash=fingerprint("正文"),
        document_type="policy",
    )
    return admin, customer, policy


@pytest.mark.django_db
@pytest.mark.parametrize("grade", ["L1", "L2", "L3"])
def test_formal_grades_and_geographic_filter(record, grade):
    admin, customer, policy = record
    publish_policy(
        policy.id,
        admin,
        1,
        provenance={
            "source_grade": grade,
            "geographic_level": "city",
            "province": "江苏省",
            "city": "南京市",
        },
    )
    client = APIClient()
    client.force_authenticate(customer)
    assert (
        client.get(
            f"/api/v1/policies?source_grade={grade}&geographic_level=city&province=江苏省&city=南京市"
        ).json()["count"]
        == 1
    )
    assert client.get("/api/v1/policies?geographic_level=national").json()["count"] == 0
    assert client.get("/api/v1/policies?province=浙江省").json()["count"] == 0
    assert client.get("/api/v1/policies?source_grade=L4").json()["count"] == 0
    assert client.get("/api/v1/policies?geographic_level=invalid").status_code == 400
    assert PublicationEvent.objects.get().payload["source_grade"] == grade


@pytest.mark.django_db
@pytest.mark.parametrize(
    "provenance",
    [
        {"source_grade": "L4", "geographic_level": "national"},
        {"source_grade": "unverified", "geographic_level": "national"},
        {"source_grade": "L1", "geographic_level": "unverified"},
        {"source_grade": "L1", "geographic_level": "provincial"},
        {"source_grade": "L1", "geographic_level": "city", "province": "江苏省"},
        {"source_grade": "L1", "geographic_level": "national", "province": "江苏省"},
    ],
)
def test_invalid_provenance_never_publishes(record, provenance):
    admin, _, policy = record
    with pytest.raises(ValidationError):
        publish_policy(policy.id, admin, 1, provenance=provenance)
    policy.refresh_from_db()
    assert policy.status == "candidate"
    assert PublicationEvent.objects.count() == 0


@pytest.mark.django_db
def test_l4_is_excluded_from_results_notifications_evidence_and_analysis(record):
    admin, customer, policy = record
    # Simulate legacy or imported bad data: even published status cannot make L4 formal.
    policy.status, policy.source_grade = "published", "L4"
    policy.save(update_fields=["status", "source_grade"])
    sub = Subscription.objects.create(user=customer, name="关注", idempotency_key="s")
    event = PublicationEvent.objects.create(
        policy=policy,
        policy_version=1,
        payload={
            "subscription_ids": [str(sub.id)],
            "title": policy.title,
        },
    )
    assert deliver_event(event.id) == 0
    Notification.objects.create(user=customer, event=event, title="旧通知")
    Evidence.objects.create(
        policy=policy, policy_version=1, text="无依据事实", quote_hash=fingerprint("x")
    )
    client = APIClient()
    client.force_authenticate(customer)
    assert client.get("/api/v1/policies").json()["count"] == 0
    assert client.get(f"/api/v1/policies/{policy.id}").status_code == 404
    assert client.get(f"/api/v1/policies/{policy.id}/evidence").status_code == 404
    assert client.get("/api/v1/notifications").json()["count"] == 0
    assert client.get("/api/v1/overview").json()["unread"] == 0
    with pytest.raises(PermissionDenied):
        authorize(admin.id, policy.id, 1)


@pytest.mark.django_db
def test_marking_lead_cannot_be_used_to_launder_source(record):
    admin, customer, policy = record
    with pytest.raises(PermissionDenied):
        mark_as_lead(policy.id, customer, 1)
    lead = mark_as_lead(policy.id, admin, 1)
    assert lead.source_grade == "L4" and lead.version == 2
    with pytest.raises(ValidationError):
        publish_policy(
            lead.id, admin, 2, provenance={"source_grade": "L1", "geographic_level": "national"}
        )
    assert PublicationEvent.objects.count() == 0
