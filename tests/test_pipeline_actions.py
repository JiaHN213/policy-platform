"""Focused troubleshooting keeps the selected record and existing write guards."""
import uuid

import pytest
from accounts.models import User
from django.utils import timezone
from ingestion.models import DiscoveredItem, Source
from policies.models import Policy, PolicyEnrichment, ReviewRecovery
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def records():
    user = User.objects.create_superuser("pipeline-admin")
    client = APIClient()
    client.force_authenticate(user)
    source = Source.objects.create(name="来源", url="https://example.gov.cn")
    policies, items = [], []
    for i in range(2):
        policy = Policy.objects.create(title="同名文件", body="政策正文", publication_date=timezone.now().date(),
            status="published", source_key=f"focus-{i}", content_hash=f"focus-{i}")
        item = DiscoveredItem.objects.create(source=source, title=policy.title, policy=policy,
            url=f"https://example.gov.cn/{i}", status="imported")
        job = PolicyEnrichment.objects.create(policy=policy, policy_version=1, status="failed", error_code="MODEL_TIMEOUT")
        ReviewRecovery.objects.create(policy=policy, policy_version=1, source_job=job)
        policies.append(policy)
        items.append(item)
    return client, policies, items


def test_focused_record_survives_publication_and_is_exact(records):
    client, policies, items = records
    response = client.get(f"/api/v1/admin/pipeline-status?item_id={items[0].pk}")
    assert response.status_code == 200
    assert len(response.data["items"]) == 1
    row = response.data["items"][0]
    assert row["policy_id"] == str(policies[0].pk)
    assert row["policy_status"] == "published" and row["intake_status"] == "imported"
    assert client.get(f"/api/v1/admin/policies/{policies[0].pk}?status=all").status_code == 200
    history = client.get(f"/api/v1/admin/review-recoveries?policy_id={policies[0].pk}")
    assert history.status_code == 200 and history.data["count"] == 1
    assert str(history.data["items"][0]["policy"]) == str(policies[0].pk)
    missing = client.get(f"/api/v1/admin/pipeline-status?item_id={uuid.uuid4()}")
    assert missing.status_code == 200 and missing.data["items"] == []


@pytest.mark.parametrize("path", ["pipeline-status?item_id=invalid", "review-recoveries?policy_id=invalid"])
def test_focused_parameter_validation(records, path):
    client, _, _ = records
    assert client.get(f"/api/v1/admin/{path}").status_code == 400


def test_focused_views_do_not_grant_customer_access(records):
    client, policies, items = records
    client.force_authenticate(User.objects.create_user("pipeline-customer"))
    for path in [f"pipeline-status?item_id={items[0].pk}", f"discovered-items/{items[0].pk}",
                 f"policies/{policies[0].pk}?status=all", f"review-recoveries?policy_id={policies[0].pk}"]:
        assert client.get(f"/api/v1/admin/{path}").status_code == 403


def test_single_parse_retry_only_requeues_failed_record(records):
    client, _, items = records
    item = items[0]
    item.status, item.error_code = "failed", "IMPORT_LEASE_EXPIRED"
    item.save()
    path = f"/api/v1/admin/discovered-items/{item.pk}/retry"
    assert client.post(path).status_code == 202
    item.refresh_from_db()
    assert item.status == "discovered" and item.error_code == ""
    assert client.post(path).status_code == 400
    items[1].refresh_from_db()
    assert items[1].status == "imported"
