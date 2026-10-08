from datetime import date, timedelta
from importlib import import_module

import pytest
from core.models import AuditRecord
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone
from knowledge.models import KnowledgeBuild
from policies import event_consumers, opensearch
from policies.event_consumers import process_consumption, retry_consumption
from policies.event_tasks import dispatch_publication_consumers
from policies.models import Policy, PublicationEvent
from policies.models import PublicationConsumption as Consumption
from policies.services import publish_policy, withdraw_policy
from rest_framework.test import APIClient
from subscriptions.models import Notification, NotificationPreference, Subscription


@pytest.fixture
def published(db):
    admin = get_user_model().objects.create_superuser("event-admin")
    reader = get_user_model().objects.create_user("event-reader")
    NotificationPreference.objects.create(user=reader, update_mode="instant")
    Subscription.objects.create(user=reader, name="水务", idempotency_key="water")
    policy = Policy.objects.create(
        title="水务设施建设办法",
        publication_date=date(2026, 1, 1),
        body="支持水务设施建设和改造。",
        source_key="event-policy",
        content_hash="event-content",
        source_url="https://www.gov.cn/test",
        document_type="policy",
        source_grade="L1",
        geographic_level="national",
    )
    publish_policy(policy.pk, admin, policy.version)
    return admin, reader, policy, PublicationEvent.objects.get()


def ready(job):
    Consumption.objects.filter(pk=job.pk).update(retry_at=None)


@pytest.mark.django_db
def test_publication_and_consumers_are_atomic_and_idempotent(published):
    admin, _, policy, event = published
    publish_policy(policy.pk, admin, policy.version)
    assert event.consumptions.count() == 4
    with pytest.raises(RuntimeError), transaction.atomic():
        PublicationEvent.objects.create(policy=policy, policy_version=2, payload={})
        raise RuntimeError("rollback")
    assert PublicationEvent.objects.count() == 1
    assert Consumption.objects.count() == 4


@pytest.mark.django_db
def test_search_failure_does_not_block_other_consumers_or_duplicate_notifications(
    published, monkeypatch, settings
):
    _, _, policy, event = published
    settings.LOCAL_WORKER = True

    def unavailable(_):
        raise RuntimeError("upstream-secret-must-not-leak")

    monkeypatch.setattr(opensearch, "sync_policy", unavailable)
    dispatch_publication_consumers()
    policy.refresh_from_db()
    assert policy.status == "published"
    search = event.consumptions.get(consumer="search")
    assert search.status == "retry" and search.retry_at > timezone.now()
    assert "secret" not in search.last_error
    assert event.consumptions.get(consumer="subscription").status == "succeeded"
    assert event.consumptions.get(consumer="statistics").status == "succeeded"
    assert event.consumptions.get(consumer="wiki").status == "pending"
    assert Notification.objects.count() == 1
    notification = event.consumptions.get(consumer="subscription")
    Consumption.objects.filter(pk=notification.pk).update(status="pending")
    process_consumption(notification.pk)
    assert Notification.objects.count() == 1
    stats = event.consumptions.get(consumer="statistics")
    Consumption.objects.filter(pk=stats.pk).update(status="pending")
    process_consumption(stats.pk)
    assert (
        AuditRecord.objects.filter(action="publication.statistics", object_id=event.pk).count() == 1
    )


@pytest.mark.django_db
def test_retry_budget_permissions_and_recovery(published, monkeypatch):
    admin, reader, _, event = published
    job = event.consumptions.get(consumer="search")

    def fail(_):
        raise RuntimeError("temporary")

    monkeypatch.setattr(opensearch, "sync_policy", fail)
    for _ in range(5):
        ready(job)
        process_consumption(job.pk)
    job.refresh_from_db()
    assert job.status == "failed" and job.attempts == 5 and job.retry_at is None
    client = APIClient()
    client.force_authenticate(reader)
    assert client.get("/api/v1/admin/publication-consumers").status_code == 403
    assert client.get("/api/v1/admin/publication-consumers/summary").status_code == 403
    assert client.post(f"/api/v1/admin/publication-consumers/{job.pk}/retry").status_code == 403
    client.force_authenticate(admin)
    assert client.get("/api/v1/admin/publication-consumers?status=failed").json()["count"] == 1
    summary = client.get("/api/v1/admin/publication-consumers/summary").json()
    assert summary["events"] == 1 and summary["counts"]["failed"] == 1
    assert client.post(f"/api/v1/admin/publication-consumers/{job.pk}/retry").status_code == 200
    monkeypatch.setattr(opensearch, "sync_policy", lambda _: {"version": 1, "message": "成功"})
    process_consumption(job.pk)
    job.refresh_from_db()
    assert job.status == "succeeded" and job.last_success_version == 1
    assert client.post(f"/api/v1/admin/publication-consumers/{job.pk}/retry").status_code == 409


@pytest.mark.django_db
def test_active_lease_and_expired_worker_result_are_fenced(published, monkeypatch):
    _, _, _, event = published
    job = event.consumptions.get(consumer="statistics")
    Consumption.objects.filter(pk=job.pk).update(
        status="running", lease_until=timezone.now() + timedelta(minutes=1)
    )
    process_consumption(job.pk)
    assert not AuditRecord.objects.filter(action="publication.statistics").exists()
    Consumption.objects.filter(pk=job.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
    process_consumption(job.pk)
    job.refresh_from_db()
    assert job.status == "succeeded"
    Consumption.objects.filter(pk=job.pk).update(status="pending")

    def replaced_worker(job):
        Consumption.objects.filter(pk=job.pk).update(
            lease_token=None, status="retry", last_error="新任务结果"
        )
        return {"message": "旧结果"}, 1, None

    monkeypatch.setattr(event_consumers, "_handle", replaced_worker)
    process_consumption(job.pk)
    job.refresh_from_db()
    assert job.status == "retry" and job.last_error == "新任务结果"


@pytest.mark.django_db
def test_wiki_waits_for_real_complete_build_and_coalesces(published):
    _, _, policy, event = published
    newer_event = PublicationEvent.objects.create(policy=policy, policy_version=2, payload={})
    jobs = [event.consumptions.get(consumer="wiki"), newer_event.consumptions.get(consumer="wiki")]
    for job in jobs:
        ready(job)
        process_consumption(job.pk)
        job.refresh_from_db()
        assert job.status == "waiting" and job.succeeded_at is None
    assert jobs[0].wiki_build_id == jobs[1].wiki_build_id
    assert KnowledgeBuild.objects.count() == 1
    KnowledgeBuild.objects.filter(pk=jobs[0].wiki_build_id).update(
        status="succeeded", result={"relation_audit": {"pending": 3}}
    )
    ready(jobs[0])
    process_consumption(jobs[0].pk)
    jobs[0].refresh_from_db()
    assert jobs[0].status == "waiting"  # partial relation scan must not count as done
    build = jobs[0].wiki_build
    KnowledgeBuild.objects.filter(pk=build.pk).update(
        status="succeeded", result={"relation_audit": {"pending": 0}}
    )
    ready(jobs[0])
    process_consumption(jobs[0].pk)
    jobs[0].refresh_from_db()
    assert jobs[0].status == "succeeded" and jobs[0].attempts == 1


@pytest.mark.django_db
def test_wiki_partial_failure_is_visible_and_can_retry(published):
    admin, _, _, event = published
    job = event.consumptions.get(consumer="wiki")
    ready(job)
    process_consumption(job.pk)
    job.refresh_from_db()
    KnowledgeBuild.objects.filter(pk=job.wiki_build_id).update(
        status="succeeded", result={"failed_pages": [{"key": "topic"}]}
    )
    ready(job)
    process_consumption(job.pk)
    job.refresh_from_db()
    assert job.status == "retry" and "引用" in job.last_error
    retry_consumption(job.pk, admin)
    process_consumption(job.pk)
    job.refresh_from_db()
    assert job.status == "waiting"


@pytest.mark.django_db
def test_withdrawal_generates_cleanup_and_search_uses_current_policy(published, monkeypatch):
    admin, _, policy, old_event = published
    withdraw_policy(policy.pk, admin, policy.version)
    withdraw_policy(policy.pk, admin, policy.version)
    withdrawn = PublicationEvent.objects.get(kind="policy.withdrawn.v1")
    assert withdrawn.consumptions.count() == 4
    monkeypatch.setattr(opensearch, "ensure_index", lambda: None)
    monkeypatch.setattr(opensearch, "_request", lambda *args, **kwargs: {})
    written = []
    monkeypatch.setattr(opensearch, "_bulk", lambda records: written.extend(records))
    process_consumption(old_event.consumptions.get(consumer="search").pk)
    assert len(written) == 1 and written[0].status == "withdrawn"


@pytest.mark.django_db(transaction=True)
def test_migration_preserves_delivered_notifications(published):
    _, _, _, event = published
    process_consumption(event.consumptions.get(consumer="subscription").pk)
    event.consumptions.all().delete()
    migration = import_module("policies.migrations.0017_seed_publication_consumers")
    with connection.schema_editor() as editor:
        migration.seed_existing(apps, editor)
        migration.seed_existing(apps, editor)
    assert event.consumptions.count() == 4
    job = event.consumptions.get(consumer="subscription")
    assert job.status == "succeeded"
    process_consumption(job.pk)
    assert Notification.objects.count() == 1
