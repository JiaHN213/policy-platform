import hashlib

import pytest
from core.ai_capacity import ModelCapacityBusy, shared_capacity
from core.ai_runtime import AIProfile
from django.db import connection

pytestmark = pytest.mark.django_db


def test_capacity_releases_postgres_locks_after_failure(monkeypatch, settings):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL advisory-lock integration")
    import psycopg

    profile = AIProfile("enterprise", "http://capacity.test:11434/v1", "test", "test", True, 1)
    monkeypatch.setattr("core.ai_capacity.get_ai_profile", lambda *a: profile)
    settings.AI_REVIEW_CONCURRENCY = 1
    def key(value):
        return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big", signed=True)
    keys = [key("policy-ai:capacity.test:11434:0"), key("policy-ai-purpose:capacity.test:11434:enterprise:0")]
    config = connection.settings_dict
    with psycopg.connect(host=config["HOST"], port=config["PORT"], dbname=config["NAME"], user=config["USER"], password=config["PASSWORD"], autocommit=True) as other:
        @shared_capacity
        def fail():
            for k in keys:
                assert not other.execute("SELECT pg_try_advisory_lock(%s)", [k]).fetchone()[0]
            raise ValueError("simulated model failure")
        with pytest.raises(ValueError):
            fail()
        for k in keys:
            assert other.execute("SELECT pg_try_advisory_lock(%s)", [k]).fetchone()[0]
        times = iter([0, 20])
        monkeypatch.setattr("core.ai_capacity.time.monotonic", lambda: next(times))
        with pytest.raises(ModelCapacityBusy):
            fail()
        for k in keys:
            other.execute("SELECT pg_advisory_unlock(%s)", [k])
