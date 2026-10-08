from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from ingestion.gov_library import SourceUnavailable
from ingestion.importer import import_record
from ingestion.models import DiscoveredItem, Source, SourceCheckRun
from ingestion.tasks import check_source, dispatch_imports, import_discovered
from policies.models import Policy, PublicationEvent
from policies.services import publish_policy
from rest_framework.test import APIClient
from subscriptions.models import Notification, NotificationPreference, Subscription
from subscriptions.services import deliver_event


@pytest.fixture
def intake(db, settings, tmp_path, monkeypatch):
    settings.DEBUG = True
    settings.LOCAL_WORKER = True
    settings.ORIGINAL_STORAGE_BACKEND = "local"
    settings.ORIGINAL_STORAGE_ROOT = tmp_path
    source = Source.objects.create(name="测试来源", url="https://sousuo.www.gov.cn/test")
    record = {
        "url": "https://www.gov.cn/zhengce/test.htm",
        "title": "供水政策测试",
        "issuer": "测试部门",
        "publication_date": date(2026, 9, 16),
        "document_number": "测1",
    }
    monkeypatch.setattr(
        "ingestion.tasks.fetch_list_page",
        lambda *args: {
            "items": [record],
            "total": 1,
        },
    )
    html = ('<div id="UCAP-CONTENT">' + "供水数字化政策正文。" * 10 + "</div>").encode()
    monkeypatch.setattr("ingestion.importer.fetch_resource", lambda url: (html, "text/html", url))
    return source, record


@pytest.mark.django_db
def test_discovery_to_review_to_notification(intake):
    source, _ = intake
    run = SourceCheckRun.objects.create(source=source)
    check_source(str(run.id))
    assert DiscoveredItem.objects.count() == 1
    assert DiscoveredItem.objects.get().metadata["publication_date"] == "2026-09-16"
    dispatch_imports()
    item = DiscoveredItem.objects.get()
    assert item.status == "imported"
    assert item.policy.status == "candidate"
    assert item.policy.topics == ["水务"]
    assert PublicationEvent.objects.count() == 0
    import_discovered(str(item.id))
    assert Policy.objects.count() == 1
    admin = get_user_model().objects.create_superuser("reviewer")
    customer = get_user_model().objects.create_user("customer")
    Subscription.objects.create(user=customer, name="关注水务", topic="水务", idempotency_key="1")
    NotificationPreference.objects.create(user=customer, update_mode="instant")
    publish_policy(
        item.policy_id,
        admin,
        1,
        document_type="policy",
        provenance={"source_grade": "L1", "geographic_level": "national"},
    )
    event = PublicationEvent.objects.get()
    assert deliver_event(event.id) == 1
    assert deliver_event(event.id) == 0
    assert Notification.objects.get().user == customer


@pytest.mark.django_db
def test_central_catalogue_scan_resumes_until_every_page_is_checked(
    settings, tmp_path, monkeypatch
):
    settings.DEBUG = True
    settings.LOCAL_WORKER = True
    settings.ORIGINAL_STORAGE_BACKEND = "local"
    settings.ORIGINAL_STORAGE_ROOT = tmp_path
    source = Source.objects.create(
        name="国务院政策文件库",
        url="https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary",
        adapter="gov_library_html_v1",
    )
    totals = {"gw": 11, "bm": 11}

    def page(category, number):
        start = (number - 1) * 5
        count = min(5, max(0, totals[category] - start))
        return {
            "items": [
                {
                    "url": f"https://www.gov.cn/zhengce/202609/{category}-{start + index}.htm",
                    "title": f"{category}政策{start + index}",
                    "issuer": "测试部门",
                    "publication_date": date(2026, 9, 1),
                    "document_number": "",
                }
                for index in range(count)
            ],
            "total": totals[category],
            "raw": b"{}",
        }

    monkeypatch.setattr("ingestion.tasks.fetch_list_page", page)
    monkeypatch.setattr("ingestion.gov_library.time.sleep", lambda *_: None)
    run = SourceCheckRun.objects.create(source=source)

    check_source(str(run.id))
    run.refresh_from_db()
    assert run.status == "queued", (run.error_code, run.error_message)
    assert run.progress["pages_scanned"] == 5
    assert DiscoveredItem.objects.count() == 21

    check_source(str(run.id))
    run.refresh_from_db()
    source.refresh_from_db()
    assert run.status == "succeeded"
    assert run.progress["pages_scanned"] == 6
    assert run.progress["rows_scanned"] == 22
    assert DiscoveredItem.objects.count() == 22
    assert source.verification_status == "verified"


@pytest.mark.django_db
def test_failed_import_retry_budget_and_staff_only_controls(intake, monkeypatch):
    source, record = intake
    record = {**record, "publication_date": record["publication_date"].isoformat()}
    item = DiscoveredItem.objects.create(source=source, url=record["url"], metadata=record)

    def fail(*args, **kwargs):
        raise SourceUnavailable("BODY_STRUCTURE_UNVERIFIED")

    monkeypatch.setattr("ingestion.tasks.import_record", fail)
    for attempt in range(1, 4):
        import_discovered(str(item.id))
        item.refresh_from_db()
        assert item.status == "failed" and item.attempts == attempt
        import_discovered(str(item.id))  # Not due yet; do not retry immediately.
        item.refresh_from_db()
        assert item.attempts == attempt
        item.retry_at = timezone.now() - timedelta(seconds=1)
        item.save(update_fields=["retry_at"])
    dispatch_imports()
    item.refresh_from_db()
    assert item.attempts == 3
    assert Policy.objects.count() == 0
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user("customer"))
    assert client.get("/api/v1/admin/discovered-items").status_code == 403
    assert client.post(f"/api/v1/admin/discovered-items/{item.id}/retry").status_code == 403
    assert client.patch(f"/api/v1/admin/sources/{source.id}", {"enabled": True}).status_code == 403
    client.force_authenticate(get_user_model().objects.create_user("operator", is_staff=True))
    summary = client.get("/api/v1/admin/discovered-items/summary")
    assert summary.status_code == 200
    assert summary.data["total"] == 1
    assert summary.data["failed"] == 1
    assert client.post(f"/api/v1/admin/discovered-items/{item.id}/retry").status_code == 202
    item.refresh_from_db()
    assert item.status == "discovered" and item.attempts == 0
    assert (
        client.patch(f"/api/v1/admin/sources/{source.id}", {"interval_minutes": 1}).status_code
        == 400
    )
    assert client.patch(f"/api/v1/admin/sources/{source.id}", {"enabled": True}).status_code == 200


@pytest.mark.django_db
def test_expired_claim_cannot_commit_policy(intake):
    source, record = intake
    old_lease = timezone.now() - timedelta(seconds=1)
    item = DiscoveredItem.objects.create(
        source=source, url=record["url"], status="processing", lease_until=old_lease
    )
    with pytest.raises(SourceUnavailable, match="IMPORT_LEASE_EXPIRED"):
        import_record(record, source, claim=(item.id, old_lease))
    assert Policy.objects.count() == 0


@pytest.mark.django_db
def test_irrelevant_central_policy_with_serialized_date_is_excluded(intake):
    source, record = intake
    record = {
        **record,
        "title": "国务院办公机构设置通知",
        "publication_date": record["publication_date"].isoformat(),
    }
    item = DiscoveredItem.objects.create(
        source=source,
        url=record["url"],
        metadata=record,
        status="processing",
        lease_until=timezone.now() + timedelta(minutes=5),
    )
    html = ('<div id="UCAP-CONTENT">' + "办公机构人员调整和职责分工。" * 10 + "</div>").encode()
    from unittest.mock import patch

    with patch("ingestion.importer.fetch_resource", return_value=(html, "text/html", record["url"])):
        import_record(record, source, claim=(item.pk, item.lease_until))
    item.refresh_from_db()
    assert item.status == "excluded"
    assert Policy.objects.count() == 0


@pytest.mark.django_db
def test_external_customers_never_see_or_receive_demo_policies(intake):
    source, record = intake
    policy, *_ = import_record(record, source)
    policy.is_demo = True
    policy.save(update_fields=["is_demo"])
    admin = get_user_model().objects.create_superuser("admin")
    customer = get_user_model().objects.create_user("customer")
    Subscription.objects.create(user=customer, name="订阅", idempotency_key="demo")
    publish_policy(
        policy.id,
        admin,
        1,
        document_type="policy",
        provenance={"source_grade": "L1", "geographic_level": "national"},
    )
    event = PublicationEvent.objects.get()
    assert deliver_event(event.id) == 0
    # Even an old demo notification is hidden after this upgrade.
    Notification.objects.create(user=customer, event=event, title=policy.title)
    client = APIClient()
    client.force_authenticate(customer)
    assert client.get("/api/v1/policies").json()["count"] == 0
    assert client.get(f"/api/v1/policies/{policy.id}").status_code == 404
    assert client.get("/api/v1/notifications").json()["count"] == 0
    assert client.get("/api/v1/overview").json()["unread"] == 0
    assert client.get("/api/v1/taxonomies").json()["regions"] == []
