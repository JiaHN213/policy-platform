from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from policies import enrichment as ai
from policies import recovery
from policies.models import (
    AIReviewControl,
    DocumentSnapshot,
    Policy,
    PolicyEnrichment,
    ReviewRecovery,
)
from policies.recovery_context import RecoveryInterrupted
from policies.tasks import runnable_enrichment_jobs
from rest_framework.test import APIClient


@pytest.fixture
def job(db, settings):
    settings.AI_BASE_URL = "http://localhost:11434/v1"
    settings.AI_MODEL = "test"
    settings.AI_API_KEY = "local"
    policy = Policy.objects.create(title="南宁市污水处理办法", body="本办法支持污水处理项目和数字化管理。",
        issuer="南宁市人民政府", publication_date=date(2026, 1, 1), source_key="recovery-policy",
        content_hash="original", status="candidate", source_grade="L1", source_url="https://www.nanning.gov.cn/a")
    return PolicyEnrichment.objects.create(policy=policy, policy_version=1, status="failed", attempts=3, error_code="MODEL_TIMEOUT")


def output(policy):
    return {"metadata": {"summary": "支持污水处理项目", "summary_evidence": [{"text": "支持污水处理项目", "quote": "支持污水处理项目"}], "structured_keywords": []},
            "review": {"decision": "include", "confidence": 0.95, "reason": "正文明确适用于污水项目。",
                       "document_type": "policy", "source_grade": "L1", "geographic_level": "city",
                       "province": "广西壮族自治区", "city": "南宁市", "validity_status": "unverified",
                       "business_domains": ["urban_sewage"], "direction_tags": [],
                       "evidence": [{"field": "scope", "quote": "支持污水处理项目"}], "facts": [], "warnings": []}}


def test_original_review_service_publishes_and_records_before_after(job, monkeypatch):
    monkeypatch.setattr(ai, "run_graph", output)
    record = recovery.enqueue(job)
    recovery.process(record.pk)
    record.refresh_from_db()
    job.policy.refresh_from_db()
    assert record.status == "succeeded" and job.policy.status == "published"
    assert record.result["before"]["error"] == "MODEL_TIMEOUT"
    assert record.result["after"]["decision"] == "include"
    recovery.process(record.pk)
    assert ReviewRecovery.objects.get(pk=record.pk).attempts == 1


@pytest.mark.parametrize("change", ["stop", "version", "permission"])
def test_stop_edit_and_permission_loss_reject_late_results(job, monkeypatch, change):
    user = get_user_model().objects.create_superuser("operator")
    record = recovery.enqueue(job, user)
    def graph(policy):
        if change == "stop":
            recovery.stop(record)
        elif change == "version":
            Policy.objects.filter(pk=policy.pk).update(version=2, summary="人工修改")
        else:
            get_user_model().objects.filter(pk=user.pk).update(is_active=False)
        return output(policy)
    monkeypatch.setattr(ai, "run_graph", graph)
    recovery.process(record.pk)
    record.refresh_from_db()
    job.policy.refresh_from_db()
    assert job.policy.status == "candidate"
    assert record.status == ("cancelled" if change == "stop" else "failed")


def test_daily_budget_defers_without_model_call(job, monkeypatch):
    from core.models import AuditRecord
    AIReviewControl.objects.create(recovery_daily_limit=1)
    AuditRecord.objects.create(action="policy.recovery.attempt_started", object_id=job.pk)
    monkeypatch.setattr(ai, "run_graph", lambda p: pytest.fail("daily budget must block model"))
    record = recovery.enqueue(job)
    recovery.process(record.pk)
    record.refresh_from_db()
    assert record.status == "queued" and record.attempts == 0
    assert record.retry_at > timezone.now()


def test_failure_cooldown_and_attempt_cap_prevent_loop(job, monkeypatch):
    def failing(policy):
        raise ValueError("MODEL_INVALID_OUTPUT")
    monkeypatch.setattr(ai, "run_graph", failing)
    record = recovery.enqueue(job)
    recovery.process(record.pk)
    with pytest.raises(ValueError, match="冷却"):
        job.refresh_from_db()
        recovery.enqueue(job)
    ReviewRecovery.objects.filter(pk=record.pk).update(retry_at=timezone.now() - timedelta(minutes=1))
    record = recovery.enqueue(job)
    recovery.process(record.pk)
    record.refresh_from_db()
    assert record.attempts == 2 and record.status == "blocked"
    recovery.process(record.pk)
    assert ReviewRecovery.objects.get(pk=record.pk).attempts == 2
    assert not runnable_enrichment_jobs().filter(pk=job.pk).exists()


def test_stored_attachment_reparse_is_versioned_and_not_repeated(job, monkeypatch):
    snapshot = DocumentSnapshot.objects.create(policy=job.policy, url="https://www.nanning.gov.cn/a.pdf", sha256="pdf", object_key="pdf", content_type="application/pdf", size_bytes=100, parse_status="failed")
    monkeypatch.setattr(recovery, "read_original", lambda key: b"%PDF-test")
    monkeypatch.setattr(recovery, "extract_attachment_text", lambda content, suffix: "附件明确支持污水处理项目建设与运营。")
    monkeypatch.setattr(ai, "run_graph", output)
    record = recovery.enqueue(job)
    assert record.category == "parse"
    recovery.process(record.pk)
    snapshot.refresh_from_db()
    record.refresh_from_db()
    job.policy.refresh_from_db()
    assert snapshot.parse_status == "parsed" and job.policy.version == 2
    assert job.policy.body.count("附件明确支持") == 1
    assert record.status == "succeeded" and record.result["target_job"]
    recovery.process(record.pk)
    job.policy.refresh_from_db()
    assert job.policy.version == 2


def test_unsupported_parser_has_clear_reason_and_never_publishes(job, monkeypatch):
    from ingestion.gov_library import SourceUnavailable
    DocumentSnapshot.objects.create(policy=job.policy, url="https://www.nanning.gov.cn/a.doc", sha256="doc", object_key="doc", content_type="application/msword", size_bytes=100, parse_status="failed")
    monkeypatch.setattr(recovery, "read_original", lambda key: b"old-word")
    def unsupported(*args):
        raise SourceUnavailable("ATTACHMENT_PARSER_REQUIRED")
    monkeypatch.setattr(recovery, "extract_attachment_text", unsupported)
    monkeypatch.setattr(ai, "run_graph", lambda p: pytest.fail("missing parser must not call AI"))
    record = recovery.enqueue(job)
    recovery.process(record.pk)
    record.refresh_from_db()
    assert record.status == "blocked" and "解析器" in record.message
    assert "ATTACHMENT" not in record.message


def test_checkpoint_is_version_bound_and_model_budget_is_finite(job):
    record = recovery.enqueue(job)
    record.status, record.attempts = "running", 1
    record.save()
    ctx = recovery.RecoveryContext(record, lambda _: None)
    key, saved = ctx.cached("提取", {"body": job.policy.body}, ai.Metadata)
    assert saved is None
    result = ai.Metadata(points=[ai.Point(text="支持污水项目", quote="本办法支持污水处理项目")], keywords=[])
    ctx.save_checkpoint(key, result)
    assert ctx.cached("提取", {"body": job.policy.body}, ai.Metadata)[1] == result
    assert ctx.cached("提取", {"body": "新的原文"}, ai.Metadata)[1] is None
    ctx.calls = 20
    with pytest.raises(RecoveryInterrupted, match="预算"):
        ctx.before_request(100)


def test_settings_and_recovery_endpoints_are_admin_only(job):
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user("customer"))
    assert client.get("/api/v1/admin/review-recoveries/settings").status_code == 403
    client.force_authenticate(get_user_model().objects.create_superuser("admin"))
    assert client.patch("/api/v1/admin/review-recoveries/settings", {"recovery_attempt_limit": 99}, format="json").status_code == 400
    assert client.post("/api/v1/admin/review-recoveries/enqueue", {"enrichment": str(job.pk)}, format="json").status_code == 202
    assert client.post("/api/v1/admin/review-recoveries/enqueue", {"enrichment": str(job.pk)}, format="json").status_code == 202
    assert ReviewRecovery.objects.count() == 1


def test_quote_recovery_uses_only_original_sentences(job, monkeypatch):
    job.error_code = "INVALID_SUMMARY_QUOTE"
    job.save()
    record = recovery.enqueue(job)
    ctx = recovery.RecoveryContext(record, lambda _: None)
    token = recovery.current.set(ctx)
    monkeypatch.setattr(ai, "model_json", lambda *a, **kw: pytest.fail("summary must use exact source"))
    try:
        result = ai.summarize(job.policy)
    finally:
        recovery.current.reset(token)
    assert all(p["quote"] in job.policy.body for p in result["summary_evidence"])


def test_actual_model_gateway_uses_checkpoint_without_duplicate_request(job, monkeypatch):
    import httpx
    record = recovery.enqueue(job)
    record.status, record.attempts = "running", 1
    record.save()
    ctx = recovery.RecoveryContext(record, lambda _: None)
    answer = ai.Metadata(points=[ai.Point(text="支持污水项目", quote="本办法支持污水处理项目")], keywords=[])
    requests = []
    original = httpx.Client
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"message": {"content": answer.model_dump_json()}})
    monkeypatch.setattr(ai.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond)))
    token = recovery.current.set(ctx)
    try:
        assert ai.model_json("提取", {"body": job.policy.body}, ai.Metadata) == answer
        assert ai.model_json("提取", {"body": job.policy.body}, ai.Metadata) == answer
    finally:
        recovery.current.reset(token)
    assert len(requests) == 1


def test_recovering_expired_owned_job_is_not_blocked_by_old_enrichment_lease(job, monkeypatch):
    record = recovery.enqueue(job)
    ReviewRecovery.objects.filter(pk=record.pk).update(status="running", attempts=1, lease_until=timezone.now() - timedelta(minutes=1))
    PolicyEnrichment.objects.filter(pk=job.pk).update(recovery_token=record.pk, status="running", lease_until=timezone.now() + timedelta(minutes=1))
    monkeypatch.setattr(ai, "run_graph", output)
    recovery.process(record.pk)
    record.refresh_from_db()
    assert record.status == "succeeded" and record.attempts == 2


def test_expired_worker_cleanup_does_not_cancel_new_attempt(job, monkeypatch):
    record = recovery.enqueue(job)
    def replaced(policy):
        ReviewRecovery.objects.filter(pk=record.pk).update(attempts=2, status="running")
        PolicyEnrichment.objects.filter(pk=job.pk).update(attempts=5, status="running")
        return output(policy)
    monkeypatch.setattr(ai, "run_graph", replaced)
    recovery.process(record.pk)
    record.refresh_from_db()
    job.refresh_from_db()
    assert record.status == "running" and record.attempts == 2
    assert job.status == "running" and job.attempts == 5


def test_invalid_grounding_checkpoint_is_not_replayed_on_next_attempt(job, monkeypatch):
    record = recovery.enqueue(job)
    def invalid(policy):
        state = ReviewRecovery.objects.get(pk=record.pk)
        state.result = {**state.result, "checkpoints": {"invalid": {"quote": "不存在的原文"}}}
        state.save()
        raise ValueError("INVALID_SUMMARY_QUOTE")
    monkeypatch.setattr(ai, "run_graph", invalid)
    recovery.process(record.pk)
    record.refresh_from_db()
    assert record.status == "failed" and record.category == "quote"
    assert record.result["checkpoints"] == {}
    assert "原文" in record.message and "INVALID_" not in record.message
