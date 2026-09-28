from datetime import date

import pytest
from django.contrib.auth import get_user_model
from policies.classification import suggest_document_type
from policies.models import DocumentSnapshot, Policy, PublicationEvent
from policies.services import fingerprint, publish_policy
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from subscriptions.models import Notification, Subscription
from subscriptions.services import deliver_event


@pytest.mark.parametrize(
    "title, expected",
    [
        ("专项资金管理办法", "policy"),
        ("关于组织申报智慧水务试点的通知", "opportunity"),
        ("拟支持项目名单公示", "result"),
        ("政策问答", "interpretation"),
        ("节水管理办法（征求意见稿）", "draft"),
        ("关于征求《节水管理办法》意见的函", "draft"),
        ("关于召开工作会议的通知", "unclassified"),
        ("专项资金管理办法解读", "unclassified"),
    ],
)
def test_conservative_title_classification(title, expected):
    assert suggest_document_type(title) == expected


@pytest.mark.django_db
def test_classification_is_required_and_saved_with_publication():
    admin = get_user_model().objects.create_superuser("reviewer")
    customer = get_user_model().objects.create_user("customer")
    policy = Policy.objects.create(
        title="测试文件",
        source_grade="L1",
        geographic_level="national",
        issuer="测试机关",
        publication_date=date(2026, 9, 16),
        body="测试正文",
        source_url="https://www.gov.cn/zhengce/test.htm",
        source_key=fingerprint("types"),
        content_hash=fingerprint("测试正文"),
    )
    with pytest.raises(ValidationError):
        publish_policy(policy.id, admin, 1)
    assert PublicationEvent.objects.count() == 0
    client = APIClient()
    client.force_authenticate(customer)
    assert (
        client.post(
            f"/api/v1/admin/policies/{policy.id}/publish", {"version": 1, "document_type": "draft"}
        ).status_code
        == 403
    )
    client.force_authenticate(admin)
    assert (
        client.post(
            f"/api/v1/admin/policies/{policy.id}/publish", {"version": 1, "document_type": "news"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/v1/admin/policies/{policy.id}/publish", {"version": 1, "document_type": "draft"}
        ).status_code
        == 200
    )
    policy.refresh_from_db()
    assert policy.document_type == "draft"
    assert PublicationEvent.objects.get().payload["document_type"] == "draft"
    client.force_authenticate(customer)
    assert client.get("/api/v1/policies?document_type=draft").json()["count"] == 1
    assert client.get("/api/v1/policies?document_type=policy").json()["count"] == 0
    assert client.get("/api/v1/policies?document_type=news").status_code == 400
    assert any(
        t["value"] == "draft" for t in client.get("/api/v1/taxonomies").json()["document_types"]
    )
    for content_type, sha in [("text/html; charset=utf-8", "a"), ("application/pdf", "b")]:
        DocumentSnapshot.objects.create(
            policy=policy,
            url=policy.source_url + "/file.pdf",
            sha256=sha * 64,
            content_type=content_type,
            size_bytes=1024,
            object_key="private-key",
            parse_status="parsed",
        )
    detail = client.get(f"/api/v1/policies/{policy.id}").json()
    assert len(detail["attachments"]) == 1
    assert "object_key" not in detail["attachments"][0]
    policy.status = "withdrawn"
    policy.save(update_fields=["status"])
    assert client.get(f"/api/v1/policies/{policy.id}").status_code == 404


@pytest.mark.django_db
def test_type_subscription_matches_publication_and_keeps_legacy_all_types():
    admin = get_user_model().objects.create_superuser("admin")
    customer = get_user_model().objects.create_user("customer")
    client = APIClient()
    client.force_authenticate(customer)
    for kind, name in [("", "全部文件"), ("opportunity", "只看机会"), ("result", "只看结果")]:
        response = client.post(
            "/api/v1/subscriptions",
            {"name": name, "document_type": kind},
            format="json",
            HTTP_IDEMPOTENCY_KEY=name,
        )
        assert response.status_code == 201
    assert (
        client.post(
            "/api/v1/subscriptions",
            {"name": "无效", "document_type": "news"},
            HTTP_IDEMPOTENCY_KEY="invalid",
        ).status_code
        == 400
    )
    wrong_topic = Subscription.objects.create(
        user=customer,
        name="不匹配的主题",
        topic="环保",
        document_type="opportunity",
        idempotency_key="topic",
    )
    assert wrong_topic.document_type == "opportunity"
    policy = Policy.objects.create(
        title="测试申报通知",
        source_grade="L1",
        geographic_level="national",
        issuer="测试机关",
        publication_date=date(2026, 9, 16),
        body="供水项目申报",
        topics=["水务"],
        source_url="https://www.gov.cn/zhengce/test.htm",
        source_key=fingerprint("subscription-type"),
        content_hash=fingerprint("供水项目申报"),
    )
    publish_policy(policy.id, admin, 1, document_type="opportunity")
    event = PublicationEvent.objects.get()
    assert deliver_event(event.id) == 1
    notification = Notification.objects.get()
    assert any("全部文件" in reason and "符合全部政策条件" in reason for reason in notification.reasons)
    assert any("只看机会" in reason and "文件类型：政策机会文件" in reason for reason in notification.reasons)
    assert deliver_event(event.id) == 0
