import pytest
from core.models import AuditRecord
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient


@pytest.fixture(autouse=True)
def clear_throttles():
    cache.clear()
    yield
    cache.clear()


def payload(**kwargs):
    return {
        "username": "customer_one",
        "password": "Mountain!Lake-4937",
        "password_confirm": "Mountain!Lake-4937",
        **kwargs,
    }


@pytest.mark.django_db
def test_register_session_csrf_and_no_privilege_escalation():
    client = APIClient(enforce_csrf_checks=True)
    assert client.post("/api/v1/auth/register", payload()).status_code == 403
    token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    result = client.post(
        "/api/v1/auth/register", payload(is_staff=True, is_superuser=True), HTTP_X_CSRFTOKEN=token
    )
    assert result.status_code == 201
    user = get_user_model().objects.get(username="customer_one")
    assert user.check_password(payload()["password"])
    assert user.password != payload()["password"]
    assert user.is_active and not user.is_staff and not user.is_superuser
    me = client.get("/api/v1/me").json()
    assert me["id"] == user.id
    assert me["capabilities"]["policy_subscription"]["allowed"]
    assert client.get("/api/v1/admin/discovered-items").status_code == 403
    assert client.get("/api/v1/admin/policies").status_code == 403
    audit = AuditRecord.objects.get(action="auth.register")
    assert audit.actor_id == user.id
    assert "password" not in audit.details


@pytest.mark.django_db
@pytest.mark.parametrize(
    "changes",
    [
        {"password_confirm": "different"},
        {"password": "12345678", "password_confirm": "12345678"},
        {"password": "short", "password_confirm": "short"},
        {"username": "invalid name!"},
        {"password": "customer_one", "password_confirm": "customer_one"},
    ],
)
def test_invalid_registration_never_creates_user(changes):
    client = APIClient()
    assert client.post("/api/v1/auth/register", payload(**changes)).status_code == 400
    assert get_user_model().objects.count() == 0
    assert AuditRecord.objects.count() == 0


@pytest.mark.django_db
def test_duplicate_registration_and_logged_in_registration_rejected():
    client = APIClient()
    assert client.post("/api/v1/auth/register", payload()).status_code == 201
    assert client.post("/api/v1/auth/register", payload(username="second")).status_code == 400
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/register", payload()).status_code == 400
    assert get_user_model().objects.count() == 1


@pytest.mark.django_db
def test_password_spaces_preserved_on_registration_and_login():
    client = APIClient()
    password = " Mountain!Lake-4937 "
    assert (
        client.post(
            "/api/v1/auth/register", payload(password=password, password_confirm=password)
        ).status_code
        == 201
    )
    client.post("/api/v1/auth/logout")
    assert (
        client.post(
            "/api/v1/auth/login", {"username": "customer_one", "password": password}
        ).status_code
        == 200
    )


@pytest.mark.django_db
def test_registration_attempts_are_rate_limited():
    client = APIClient()
    for _ in range(5):
        assert client.post("/api/v1/auth/register", {}).status_code == 400
    assert client.post("/api/v1/auth/register", payload()).status_code == 429
    assert get_user_model().objects.count() == 0
