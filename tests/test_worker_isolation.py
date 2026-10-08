import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from accounts.models import User
from celery import Celery
from core import worker_status as status_module
from django.core.cache import cache
from rest_framework.test import APIClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def mocked_workers(monkeypatch, replies):
    connection = MagicMock()
    monkeypatch.setattr(status_module.app, "connection_for_read", connection)
    inspector = MagicMock()
    inspector.return_value.active_queues.return_value = replies
    monkeypatch.setattr(status_module.app.control, "inspect", inspector)
    return inspector, connection


def test_compose_routes_and_consumers_agree(settings):
    config = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = config["services"]
    routes = dict(settings.CELERY_TASK_ROUTES)
    assert routes["ingestion.tasks.*"]["queue"] == settings.CELERY_INGESTION_QUEUE
    routes["ingestion.tasks.*"] = {
        "queue": services["api"]["environment"]["CELERY_INGESTION_QUEUE"]
    }
    for task in (
        "subscriptions.tasks.*",
        "policies.event_tasks.dispatch_publication_consumers",
        "policies.event_tasks.consume_notification",
    ):
        routes[task] = {"queue": services["api"]["environment"]["CELERY_NOTIFICATION_QUEUE"]}
    with Celery("isolated-routing", set_as_current=False, broker="memory://") as app:
        app.conf.task_routes = routes
        for task, expected in [
            ("ingestion.tasks.dispatch_due_sources", "ingestion"),
            ("ingestion.tasks.check_source", "ingestion"),
            ("ingestion.tasks.dispatch_imports", "ingestion"),
            ("ingestion.tasks.import_discovered", "ingestion"),
            ("subscriptions.tasks.deliver_deadline_reminders", "notifications"),
            ("policies.event_tasks.consume_notification", "notifications"),
            ("policies.tasks.enrich_policy", "celery"),
            ("enterprises.watch_tasks.process_watch", "celery"),
            ("knowledge.tasks.build_pages", "celery"),
        ]:
            assert app.amqp.router.route({}, task)["queue"].name == expected
    for name, queue in [
        ("worker", "celery"),
        ("ingestion", "ingestion"),
        ("notifications", "notifications"),
    ]:
        assert f"--queues={queue}" in services[name]["command"]
        assert services[name]["environment"]["CELERY_INGESTION_QUEUE"] == "ingestion"
    assert "--concurrency=1" in services["ingestion"]["command"]
    assert any("originals" in mount for mount in services["ingestion"]["volumes"])
    dev = yaml.safe_load((ROOT / "compose.dev.yaml").read_text(encoding="utf-8"))
    assert "./apps/api:/app/apps/api" in dev["services"]["ingestion"]["volumes"]


def test_only_periodic_dispatch_expires_and_backup_stops_ingestion(settings):
    for name in ["import-discovered-policies", "dispatch-due-sources"]:
        entry = settings.CELERY_BEAT_SCHEDULE[name]
        assert entry["options"]["expires"] == entry["schedule"]
    assert "expires" not in settings.CELERY_TASK_ROUTES["ingestion.tasks.*"]
    spec = importlib.util.spec_from_file_location(
        "compose_data_test", ROOT / "scripts/compose_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "ingestion" in module.APP_SERVICES
    assert "ingestion" in module.RESTORE_SERVICES


def test_service_status_counts_without_exposing_hosts_or_task_details(settings, monkeypatch):
    settings.CELERY_INGESTION_QUEUE = "ingestion"
    settings.CELERY_NOTIFICATION_QUEUE = "notifications"
    inspector, _ = mocked_workers(
        monkeypatch,
        {
            "private-host-a": [{"name": "ingestion"}],
            "private-host-b": [{"name": "celery"}],
            "private-host-c": [{"name": "notifications"}],
        },
    )
    result = status_module.worker_status()
    assert all(item["state"] == "responding" and not item["shared"] for item in result["items"])
    assert "private-host" not in str(result)
    assert status_module.worker_status() == result
    assert inspector.call_count == 1


def test_shared_consumer_and_no_reply_are_not_misreported(settings, monkeypatch):
    settings.CELERY_INGESTION_QUEUE = "ingestion"
    settings.CELERY_NOTIFICATION_QUEUE = "notifications"
    mocked_workers(monkeypatch, {"shared": [{"name": "ingestion"}, {"name": "celery"}]})
    result = status_module.worker_status()["items"]
    assert result[0]["shared"] and result[1]["shared"]
    assert result[2]["state"] == "unconfirmed"
    cache.clear()
    settings.CELERY_INGESTION_QUEUE = "celery"
    result = status_module.worker_status()["items"]
    assert result[0]["shared"]


def test_broker_failure_has_actionable_chinese_message(monkeypatch):
    inspector, connection = mocked_workers(monkeypatch, {})
    connection.side_effect = RuntimeError("redis://secret-password@private-host/")
    result = status_module.worker_status()
    assert all(item["state"] == "unconfirmed" for item in result["items"])
    assert "secret-password" not in str(result)
    assert "稍后重查" in result["items"][0]["message"]
    inspector.assert_not_called()


@pytest.mark.django_db
def test_runtime_api_is_admin_only(monkeypatch):
    mocked_workers(monkeypatch, {})
    client = APIClient()
    assert client.get("/api/v1/admin/worker-status").status_code in [401, 403]
    user = User.objects.create_user("runtime-viewer")
    client.force_authenticate(user)
    assert client.get("/api/v1/admin/worker-status").status_code == 403
    user.is_staff = user.is_superuser = True
    user.save()
    assert client.get("/api/v1/admin/worker-status").status_code == 200
