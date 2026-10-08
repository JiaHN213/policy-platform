import importlib.util
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from accounts.models import Membership, Organization, User
from core.ai_runtime import AIProfile
from core.ai_usage import request_json
from core.models import AICall
from core.operations import pipeline_rows
from django.utils import timezone
from enterprises.models import EnterpriseProfile, EnterpriseProject
from ingestion.models import DiscoveredItem, Source
from policies.models import Policy, PolicyEnrichment, PublicationEvent
from rest_framework.test import APIClient
from subscriptions.following import configure_follow, sync_pending_follows
from subscriptions.models import Notification, PendingDelivery, ProfileFollow, Subscription

pytestmark = pytest.mark.django_db


@pytest.fixture
def company():
    user = User.objects.create_user("project-owner")
    org = Organization.objects.create(name="企业")
    Membership.objects.create(user=user, organization=org, role="admin")
    profile = EnterpriseProfile.objects.create(organization=org, data={"business_domains": ["urban_sewage"]}, confirmed_at=timezone.now())
    project = EnterpriseProject.objects.create(profile=profile, name="供水改造", data={"business_domains": ["water_supply"], "city": "南宁"})
    client = APIClient()
    client.force_authenticate(user)
    return user, profile, project, client


def test_project_follow_is_separate_and_tracks_changes(company):
    user, profile, project, client = company
    configure_follow(user, profile, True)
    path = f"/api/v1/enterprise-projects/{project.pk}/follow-subscriptions"
    response = client.post(path, {"enabled": True, "revision": 1}, format="json")
    assert response.status_code == 200 and response.data["follow_subscriptions"]
    sub = Subscription.objects.get(source_project=project)
    assert sub.business_domain == "water_supply" and sub.interest_regions == ["南宁"]
    assert Subscription.objects.get(source_profile=profile, source_project=None).business_domain == "urban_sewage"
    project.data = {"business_domains": ["urban_sewage"], "interest_regions": ["柳州"]}
    project.revision = 2
    project.save()
    sync_pending_follows()
    sub.refresh_from_db()
    assert not sub.active and sub.system_paused
    assert Subscription.objects.get(source_project=project, business_domain="urban_sewage").interest_regions == ["柳州"]
    assert client.post(path, {"enabled": False, "revision": 1}, format="json").status_code == 409
    assert client.post(path, {"enabled": False, "revision": 2}, format="json").status_code == 200
    assert Subscription.objects.filter(source_project=project, active=True).exists()


def test_project_manual_edits_soft_deletion_and_access_loss(company):
    user, profile, project, client = company
    configure_follow(user, profile, True, project)
    sub = Subscription.objects.get(source_project=project)
    assert client.patch(f"/api/v1/subscriptions/{sub.pk}", {"active": False, "revision": 1}, format="json").status_code == 200
    project.revision += 1
    project.save()
    sync_pending_follows()
    sub.refresh_from_db()
    assert not sub.active and not sub.managed
    assert client.delete(f"/api/v1/subscriptions/{sub.pk}").status_code == 204
    project.revision += 1
    project.save()
    sync_pending_follows()
    assert Subscription.objects.filter(source_project=project).count() == 1
    Membership.objects.filter(user=user).update(active=False)
    assert client.post(f"/api/v1/enterprise-projects/{project.pk}/follow-subscriptions", {"enabled": True, "revision": project.revision}, format="json").status_code == 404
    sync_pending_follows()
    assert not ProfileFollow.objects.get(project=project).enabled


def test_delete_project_removes_only_its_rules(company):
    user, profile, project, client = company
    configure_follow(user, profile, True)
    configure_follow(user, profile, True, project)
    assert client.delete(f"/api/v1/enterprise-projects/{project.pk}").status_code == 204
    assert Subscription.objects.count() == 1
    assert ProfileFollow.objects.count() == 1


@pytest.mark.parametrize("local", [False, True])
def test_usage_provider_tokens_and_price_snapshot(local):
    profile = AIProfile("review", "http://localhost:11434/v1" if local else "https://example.com/v1", "secret", "model", True, input_price=Decimal(2), output_price=Decimal(4))
    client = MagicMock()
    client.post.return_value.json.return_value = {"prompt_eval_count": 100, "eval_count": 20} if local else {"usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    request_json(client, profile, "http://model", headers={"Authorization": "secret"}, payload={"messages": ["private content"]})
    row = AICall.objects.get()
    assert row.input_tokens == 100 and row.output_tokens == 20
    assert row.estimated_cost == Decimal("0.00028")
    assert "private content" not in str(row.__dict__) and "secret" not in str(row.__dict__)


def test_missing_usage_and_timeout_are_not_zero_usage():
    profile = AIProfile("enterprise", "https://example.com", "secret", "model", True)
    client = MagicMock()
    client.post.return_value.json.return_value = {"usage": {"prompt_tokens": True, "completion_tokens": -1}}
    request_json(client, profile, "http://model", headers={}, payload={})
    row = AICall.objects.get()
    assert row.input_tokens is None and row.output_tokens is None and row.estimated_cost is None
    client.post.side_effect = httpx.ReadTimeout("secret provider error")
    with pytest.raises(httpx.ReadTimeout):
        request_json(client, profile, "http://model", headers={}, payload={})
    assert AICall.objects.filter(status="timeout").count() == 1


def test_pipeline_unknown_history_and_daily_digest_timestamp(company):
    user, _, _, _ = company
    now = timezone.now()
    source = Source.objects.create(name="来源", url="https://example.gov.cn")
    policy = Policy.objects.create(title="政策", body="正文", publication_date=now.date(), status="published", source_key="pipeline", content_hash="pipeline")
    item = DiscoveredItem.objects.create(source=source, url="https://example.gov.cn/1", title="政策", policy=policy, status="imported")
    PolicyEnrichment.objects.create(policy=policy, policy_version=1, status="succeeded")
    event = PublicationEvent.objects.create(policy=policy, policy_version=1, payload={})
    pending = PendingDelivery.objects.create(user=user, event=event)
    row = pipeline_rows("recent")[0]
    assert row["parsed_at"] is None and row["reviewed_at"] is None
    assert row["stage"] == "等待每日汇总" and row["notified_at"] is None
    note = Notification.objects.create(user=user, kind="digest", title="汇总")
    pending.notification = note
    pending.handled_at = now
    pending.save()
    row = pipeline_rows("recent")[0]
    assert row["notified_at"] == note.created_at and row["elapsed_seconds"] >= 0
    assert row["id"] == str(item.pk)


def test_pipeline_excludes_ai_exclusions_from_pending_and_marks_delay():
    now = timezone.now()
    source = Source.objects.create(name="来源", url="https://example.gov.cn")
    item = DiscoveredItem.objects.create(source=source, title="待处理", url="https://example.gov.cn/1")
    DiscoveredItem.objects.filter(pk=item.pk).update(created_at=now-timedelta(days=2))
    assert pipeline_rows()[0]["overdue"]
    policy = Policy.objects.create(title="政策", publication_date=now.date(), source_key="excluded", content_hash="excluded")
    item.policy = policy
    item.save()
    PolicyEnrichment.objects.create(policy=policy, policy_version=1, status="succeeded", result={"review": {"decision": "exclude"}})
    assert pipeline_rows() == []


def test_ops_endpoints_require_system_admin(company):
    user, _, _, client = company
    for url in ["ai-usage", "pipeline-status"]:
        assert client.get(f"/api/v1/admin/{url}").status_code == 403
    user.is_staff = user.is_superuser = True
    user.save()
    for url in ["ai-usage", "pipeline-status"]:
        assert client.get(f"/api/v1/admin/{url}").status_code == 200


def test_usage_totals_preserve_unknown_coverage_and_currency(company):
    user, _, _, client = company
    user.is_staff = user.is_superuser = True
    user.save()
    for currency, inputs, outputs, cost in [
        ("CNY", 100, 20, Decimal("0.00028")),
        ("CNY", None, None, None),
        ("USD", 50, 10, Decimal("0.00014")),
    ]:
        AICall.objects.create(purpose="review", model="model", currency=currency,
            duration_ms=100, status="received", input_tokens=inputs,
            output_tokens=outputs, estimated_cost=cost)
    response = client.get("/api/v1/admin/ai-usage")
    assert response.status_code == 200
    groups = {row["currency"]: row for row in response.data["items"]}
    assert len(groups) == 2
    assert groups["CNY"]["calls"] == 2
    assert groups["CNY"]["input_tokens"] == 100
    assert groups["CNY"]["output_tokens"] == 20
    assert groups["CNY"]["input_calls"] == groups["CNY"]["output_calls"] == 1
    assert groups["CNY"]["priced_calls"] == 1
    assert groups["USD"]["input_tokens"] == 50


def test_backup_integrity_detects_corruption_and_paths(tmp_path):
    spec = importlib.util.spec_from_file_location("backup_integrity", Path(__file__).resolve().parents[1] / "scripts/compose_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dump = tmp_path / "database.dump"
    dump.write_bytes(b"test-dump")
    manifest = {"format": 2, "includes_originals": False, "sha256": {"database.dump": module.file_hash(dump)}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert module.backup_contents(tmp_path)[0] == dump
    dump.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        module.backup_contents(tmp_path)
    with pytest.raises(ValueError, match="path"):
        module.verify_hashes(tmp_path, {"sha256": {"../outside": "x"}})
