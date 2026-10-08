from datetime import timedelta
from unittest.mock import patch

import pytest
from accounts.models import Membership, Organization, User
from core.models import AuditRecord
from django.utils import timezone
from enterprises.models import EnterpriseProfile, ResearchRun, ResearchSettings
from enterprises.quota import ensure_available, record_maintenance, statistics, window
from enterprises.tasks import schedule_refreshes
from enterprises.workflow import start
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    user = User.objects.create_user("quota-owner")
    client = APIClient()
    client.force_authenticate(user)
    ResearchSettings.objects.create(daily_limit=1)
    return user, client


def run(user, **values):
    return ResearchRun.objects.create(user=user, fingerprint="old", inputs={"name": "测试水务有限公司", "source_mode": "text", "introduction": "本公司从事城乡供水和污水处理等水务运营管理业务。"}, **values)


@pytest.mark.parametrize("status,charged", [("queued", True), ("running", True), ("waiting", True), ("completed", True), ("paused", True), ("failed", False)])
def test_only_normal_reserved_or_successful_tasks_count(owner, status, charged):
    user, _ = owner
    root = run(user, status=status)
    run(user, parent=root, status="completed")
    run(user, status="completed", quota_category="maintenance")
    other = User.objects.create_user("another-quota-owner")
    run(other, status="completed")
    assert statistics(window(user)) == {"normal": int(charged), "failed": int(not charged), "maintenance": 1, "maintenance_failed": 0}
    if charged:
        with pytest.raises(ValidationError):
            ensure_available(user, 1)
    else:
        ensure_available(user, 1)


def test_rolling_window_releases_old_success(owner):
    user, _ = owner
    root = run(user, status="completed")
    ResearchRun.objects.filter(pk=root.pk).update(created_at=timezone.now() - timedelta(hours=25))
    ensure_available(user, 1)


def test_failed_creation_releases_slot_and_active_request_reserves_it(owner):
    user, client = owner
    run(user, status="failed")
    with patch("enterprises.views.get_ai_profile") as ai, patch("enterprises.views.enqueue"):
        ai.return_value.configured = True
        response = client.post("/api/v1/enterprise-research", {"kind": "company", "name": "新水务有限公司", "source_mode": "text", "introduction": "公司从事供水和污水处理项目运营，提供水务设施建设服务。"}, format="json")
        assert response.status_code == 202
        blocked = client.post("/api/v1/enterprise-research", {"kind": "company", "name": "另一水务有限公司", "source_mode": "text", "introduction": "公司从事供水和污水处理项目运营，提供水务设施建设服务。"}, format="json")
    assert blocked.status_code == 400 and "最近24小时" in blocked.data["message"]


def test_maintenance_rerun_admin_only_and_audited(owner):
    user, client = owner
    old = run(user, status="completed")
    url = f"/api/v1/enterprise-research/{old.pk}/regenerate"
    request = {"maintenance": True, "maintenance_reason": "验证官网读取修复"}
    assert client.post(url, request, format="json").status_code == 403
    user.is_staff = user.is_superuser = True
    user.save()
    assert client.post(url, {"maintenance": True}, format="json").status_code == 400
    with patch("enterprises.views.get_ai_profile") as ai, patch("enterprises.views.enqueue"):
        ai.return_value.configured = True
        response = client.post(url, request, format="json")
    assert response.status_code == 202
    maintenance = ResearchRun.objects.get(pk=response.data["id"])
    assert maintenance.quota_category == "maintenance" and maintenance.maintenance_reason == request["maintenance_reason"]
    assert AuditRecord.objects.filter(actor=user, object_id=maintenance.pk, action="enterprise.maintenance.recorded").exists()
    assert statistics(window(user))["normal"] == 1
    assert "quota_category" not in response.data
    assert "maintenance_reason" not in response.data


def test_existing_repair_reclassification_preserves_result(owner):
    user, _ = owner
    old = run(user, status="completed", result={"notice": "已核验的草稿"})
    with pytest.raises(PermissionDenied):
        record_maintenance(old, user, "修复验证")
    user.is_staff = user.is_superuser = True
    user.save()
    record_maintenance(old, user, "修复验证")
    old.refresh_from_db()
    assert old.result == {"notice": "已核验的草稿"} and old.status == "completed"
    ensure_available(user, 1)


def profile(user):
    organization = Organization.objects.create(name="额度测试企业")
    Membership.objects.create(user=user, organization=organization, role="admin")
    return EnterpriseProfile.objects.create(organization=organization, confirmed_at=timezone.now(), data={"website": "https://example.com"}, research_method="website", refresh_days=7, next_research_at=timezone.now() - timedelta(minutes=1))


def test_workflow_uses_same_failure_exemption_and_reservation(owner):
    user, _ = owner
    company = profile(user)
    run(user, status="failed")
    with patch("enterprises.workflow.get_ai_profile") as ai, patch("enterprises.tasks.enqueue"):
        ai.return_value.configured = True
        created = start(user, company)
        assert created.quota_category == "normal"
        with pytest.raises(ValidationError):
            start(user, company, filters={"keyword": "different"})


def test_scheduled_refresh_uses_same_failure_exemption(owner):
    user, _ = owner
    company = profile(user)
    run(user, status="failed")
    with patch("enterprises.tasks.get_ai_profile") as ai, patch("enterprises.tasks.enqueue"):
        ai.return_value.configured = True
        schedule_refreshes()
    assert ResearchRun.objects.filter(user=user, profile=company, status="queued").count() == 1


def test_resume_failed_task_checks_current_quota(owner):
    user, client = owner
    user.is_staff = user.is_superuser = True
    user.save()
    failed = run(user, status="failed", runtime_snapshot={"version": "test"})
    run(user, status="completed")
    with patch("enterprises.runtime.input_issue", return_value=""):
        response = client.post(f"/api/v1/enterprise-research/{failed.pk}/resume", {}, format="json")
    assert response.status_code == 400 and "次数已用完" in response.data["message"]
    failed.refresh_from_db()
    assert failed.status == "failed"
