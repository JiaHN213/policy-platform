from unittest.mock import patch

import pytest
from accounts.models import Membership, Organization, User
from core.models import AuditRecord
from django.utils import timezone
from enterprises.models import EnterpriseProfile, EnterpriseProject, ResearchRun
from policies.models import Policy, PublicationEvent
from rest_framework.test import APIClient
from subscriptions.models import Notification, PendingDelivery, Subscription

pytestmark = pytest.mark.django_db


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def scope():
    admin = User.objects.create_superuser(username="manager", password="Local-Strong-94!")
    alice = User.objects.create_user(username="alice", password="Alice-Strong-94!")
    bob = User.objects.create_user(username="bob", password="Bob-Strong-94!")
    organization = Organization.objects.create(name="供水企业")
    Membership.objects.create(user=alice, organization=organization, role="admin")
    profile = EnterpriseProfile.objects.create(organization=organization, data={}, confirmed_at=timezone.now())
    return admin, alice, bob, profile


@pytest.mark.parametrize("path", ["enterprises", "enterprise-projects", "subscriptions", "notifications", "enterprise-research/saved-results", "policy-watches"])
def test_private_collections_reject_another_account_id(scope, path):
    _, alice, bob, _ = scope
    assert client_for(bob).get(f"/api/v1/{path}?user_id={alice.pk}").status_code == 403


def test_admin_routes_not_granted_to_plain_staff(scope):
    _, _, bob, _ = scope
    bob.is_staff = True
    bob.save()
    client = client_for(bob)
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.post("/api/v1/admin/users", {"username": "x"}).status_code == 403


def test_admin_creates_hashes_password_and_can_disable_other_account(scope):
    admin, _, _, _ = scope
    client = client_for(admin)
    response = client.post("/api/v1/admin/users", {"username": "new-client", "password": "My-Complex-482!", "role": "customer"}, format="json")
    assert response.status_code == 201, response.data
    target = User.objects.get(pk=response.data["id"])
    assert target.check_password("My-Complex-482!") and not target.is_staff
    assert "password" not in response.data
    response = client.patch(f"/api/v1/admin/users/{target.pk}", {"is_active": False, "permission_version": target.permission_version}, format="json")
    assert response.status_code == 200
    target.refresh_from_db()
    assert not target.is_active
    assert "My-Complex" not in str(list(AuditRecord.objects.values_list("details", flat=True)))


def test_self_cannot_gain_roles_or_disable_last_admin(scope):
    admin, alice, _, _ = scope
    assert client_for(alice).patch("/api/v1/account", {"role": "admin"}, format="json").status_code == 400
    assert client_for(admin).patch(f"/api/v1/admin/users/{admin.pk}", {"is_active": False}, format="json").status_code == 400
    assert client_for(admin).delete(f"/api/v1/admin/users/{admin.pk}", {"confirm_username": admin.username}, format="json").status_code == 400


def test_delegate_role_does_not_grant_superuser(scope):
    admin, alice, _, _ = scope
    response = client_for(admin).patch(f"/api/v1/admin/users/{alice.pk}", {"role": "admin", "is_superuser": True}, format="json")
    assert response.status_code == 200
    alice.refresh_from_db()
    assert alice.has_perm("accounts.manage_system") and not alice.is_superuser


def test_admin_enterprise_scope_creates_for_owner_and_records_real_actor(scope):
    admin, alice, _, _ = scope
    response = client_for(admin).post(f"/api/v1/enterprises?user_id={alice.pk}", {"name": "污水企业", "data": {}}, format="json")
    assert response.status_code == 201, response.data
    profile = EnterpriseProfile.objects.get(pk=response.data["id"])
    assert Membership.objects.filter(user=alice, organization=profile.organization).exists()
    assert not Membership.objects.filter(user=admin, organization=profile.organization).exists()
    assert AuditRecord.objects.get(action="enterprise.created", object_id=profile.pk).actor == admin


def test_profile_revision_and_other_user_protection(scope):
    admin, alice, bob, profile = scope
    data = {"name": "供水企业更名", "data": {"city": "南宁市"}, "revision": profile.revision}
    assert client_for(bob).patch(f"/api/v1/enterprises/{profile.pk}", data, format="json").status_code == 404
    client = client_for(admin)
    url = f"/api/v1/enterprises/{profile.pk}?user_id={alice.pk}"
    assert client.patch(url, data, format="json").status_code == 200
    assert client.patch(url, data, format="json").status_code == 409
    profile.refresh_from_db()
    assert profile.organization.name == "供水企业更名"


def test_admin_project_crud_and_cross_scope_denial(scope):
    admin, alice, bob, profile = scope
    client = client_for(admin)
    response = client.post(f"/api/v1/enterprise-projects?user_id={alice.pk}", {"profile": str(profile.pk), "name": "管网改造", "data": {}}, format="json")
    assert response.status_code == 201, response.data
    pk = response.data["id"]
    assert client_for(bob).delete(f"/api/v1/enterprise-projects/{pk}").status_code == 404
    assert client.patch(f"/api/v1/enterprise-projects/{pk}?user_id={alice.pk}", {"name": "一期改造", "revision": 1}, format="json").status_code == 200
    assert client.delete(f"/api/v1/enterprise-projects/{pk}?user_id={alice.pk}").status_code == 204


def test_admin_subscription_scoped_update_audits_actor(scope):
    admin, alice, bob, _ = scope
    client = client_for(admin)
    response = client.post(f"/api/v1/subscriptions?user_id={alice.pk}", {"name": "供水政策", "keywords": "供水"}, format="json", HTTP_IDEMPOTENCY_KEY="test-sub")
    assert response.status_code == 201, response.data
    sub = Subscription.objects.get(pk=response.data["id"])
    assert sub.user == alice
    assert client_for(bob).patch(f"/api/v1/subscriptions/{sub.pk}", {"active": False}, format="json").status_code == 404
    assert client.patch(f"/api/v1/subscriptions/{sub.pk}?user_id={alice.pk}", {"active": False}, format="json").status_code == 200
    assert sub.changes.get().actor == admin
    assert client.delete(f"/api/v1/subscriptions/{sub.pk}?user_id={alice.pk}").status_code == 204
    sub.refresh_from_db()
    assert sub.deleted_at and not sub.active


def test_enterprise_delete_cascades_private_data_without_deleting_policies(scope):
    _, alice, _, profile = scope
    EnterpriseProject.objects.create(profile=profile, name="项目")
    Subscription.objects.create(user=alice, name="自动规则", source_profile=profile, idempotency_key="profile")
    assert client_for(alice).delete(f"/api/v1/enterprises/{profile.pk}", {"confirm_name": profile.organization.name}, format="json").status_code == 204
    assert not EnterpriseProfile.objects.filter(pk=profile.pk).exists()
    assert not Subscription.objects.filter(user=alice).exists()
    assert User.objects.filter(pk=alice.pk).exists()


def test_active_result_prevents_cascade_delete(scope):
    _, alice, _, profile = scope
    ResearchRun.objects.create(user=alice, profile=profile, fingerprint="busy", status="running")
    response = client_for(alice).delete(f"/api/v1/enterprises/{profile.pk}", {"confirm_name": profile.organization.name}, format="json")
    assert response.status_code == 409
    assert EnterpriseProfile.objects.filter(pk=profile.pk).exists()


def test_own_password_change_requires_current_password(scope):
    _, alice, _, _ = scope
    client = client_for(alice)
    response = client.patch("/api/v1/account", {"password": "New-Complex-482!", "current_password": "wrong"}, format="json")
    assert response.status_code == 400
    response = client.patch("/api/v1/account", {"password": "New-Complex-482!", "current_password": "Alice-Strong-94!"}, format="json")
    assert response.status_code == 200
    alice.refresh_from_db()
    assert alice.check_password("New-Complex-482!")


def test_user_delete_preserves_shared_company_and_audit(scope):
    admin, alice, bob, profile = scope
    Membership.objects.create(user=bob, organization=profile.organization, role="member")
    response = client_for(admin).delete(f"/api/v1/admin/users/{alice.pk}", {"confirm_username": "alice"}, format="json")
    assert response.status_code == 204, response.data
    assert EnterpriseProfile.objects.filter(pk=profile.pk).exists()
    assert not User.objects.filter(pk=alice.pk).exists()
    assert AuditRecord.objects.filter(action="account.deleted", actor=admin).exists()


def test_messages_are_owner_scoped_and_deletable_with_digest_links(scope):
    admin, alice, bob, _ = scope
    policy = Policy.objects.create(title="供水政策", issuer="部门", publication_date=timezone.now().date(), status="published", source_grade="L1", source_url="https://example.gov.cn/p", source_key="message", content_hash="hash", body="原文")
    event = PublicationEvent.objects.create(policy=policy, policy_version=1, payload={})
    note = Notification.objects.create(user=alice, title="新政策", event=event)
    pending = PendingDelivery.objects.create(user=alice, event=event, notification=note)
    assert client_for(bob).delete(f"/api/v1/notifications/{note.pk}").status_code == 404
    assert client_for(admin).delete(f"/api/v1/notifications/{note.pk}?user_id={alice.pk}").status_code == 204
    pending.refresh_from_db()
    assert pending.notification_id is None and pending.handled_at


def test_saved_result_scope_delete_and_regeneration_uses_owner(scope):
    admin, alice, bob, profile = scope
    run = ResearchRun.objects.create(user=alice, profile=profile, fingerprint="done", status="completed", kind="company", inputs={"source_mode": "text", "name": profile.organization.name, "introduction": "提供供水污水服务以及节能改造工程建设的企业资料介绍。"}, result={"notice": "历史结果"})
    assert client_for(bob).get(f"/api/v1/enterprise-research/{run.pk}/saved-result").status_code == 404
    client = client_for(admin)
    assert client.get(f"/api/v1/enterprise-research/{run.pk}/saved-result?user_id={alice.pk}").status_code == 200
    with patch("enterprises.views.get_ai_profile") as model, patch("enterprises.views.enqueue"):
        model.return_value.configured = True
        response = client.post(f"/api/v1/enterprise-research/{run.pk}/regenerate?user_id={alice.pk}", {}, format="json")
    assert response.status_code == 202, response.data
    assert ResearchRun.objects.get(pk=response.data["id"]).user == alice
    assert response.data["id"] != str(run.pk)
    assert client.delete(f"/api/v1/enterprise-research/{run.pk}?user_id={alice.pk}").status_code == 204


def test_inactive_admin_role_is_preserved_when_reenabled(scope):
    admin, alice, _, _ = scope
    client = client_for(admin)
    client.patch(f"/api/v1/admin/users/{alice.pk}", {"role": "admin", "is_active": False}, format="json")
    response = client.get(f"/api/v1/admin/users/{alice.pk}")
    assert response.data["role"] == "admin" and not response.data["is_active"]
    assert client.patch(f"/api/v1/admin/users/{alice.pk}", {"role": response.data["role"], "is_active": True}, format="json").status_code == 200
    alice.refresh_from_db()
    assert alice.has_perm("accounts.manage_system")


def test_private_profile_edit_and_close_deletes_only_owned_data(scope):
    _, alice, bob, profile = scope
    client = client_for(alice)
    response = client.patch("/api/v1/account", {"first_name": "客户", "email": "customer@example.com", "permission_version": alice.permission_version}, format="json")
    assert response.status_code == 200
    assert response.data["first_name"] == "客户"
    Subscription.objects.create(user=alice, name="个人规则", idempotency_key="self-close")
    assert client.delete("/api/v1/account", {"confirm_username": "alice", "current_password": "wrong"}, format="json").status_code == 400
    assert client.delete("/api/v1/account", {"confirm_username": "alice", "current_password": "Alice-Strong-94!"}, format="json").status_code == 204
    assert not User.objects.filter(pk=alice.pk).exists()
    assert not EnterpriseProfile.objects.filter(pk=profile.pk).exists()
    assert User.objects.filter(pk=bob.pk).exists()
    assert AuditRecord.objects.filter(action="account.closed", actor__isnull=True).exists()


def test_notification_preferences_stay_with_selected_owner(scope):
    from subscriptions.models import NotificationPreference
    admin, alice, bob, _ = scope
    url = f"/api/v1/notifications/preferences?user_id={alice.pk}"
    assert client_for(bob).patch(url, {"digest_hour": 15}, format="json").status_code == 403
    assert client_for(admin).patch(url, {"digest_hour": 15}, format="json").status_code == 200
    assert NotificationPreference.objects.get(user=alice).digest_hour == 15
    assert not NotificationPreference.objects.filter(user=bob).exists()


def test_saved_analysis_cannot_reveal_withdrawn_policy(scope):
    _, alice, _, profile = scope
    policy = Policy.objects.create(title="撤下政策", issuer="部门", publication_date=timezone.now().date(), status="withdrawn", source_grade="L1", source_url="https://example.gov.cn/old", source_key="old", content_hash="old", body="原文")
    run = ResearchRun.objects.create(user=alice, profile=profile, fingerprint="old-analysis", kind="explanation", status="completed", inputs={"policy_id": str(policy.pk)}, result={"points": [{"text": "历史内容"}]})
    assert client_for(alice).get(f"/api/v1/enterprise-research/{run.pk}/saved-result").status_code == 403
