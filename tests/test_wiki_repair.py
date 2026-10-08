from datetime import date

import pytest
from django.contrib.auth import get_user_model
from knowledge import relations, repair, services
from knowledge.models import (
    KnowledgePage,
    KnowledgeRelationScan,
    RelationRepair,
    RelationReviewCandidate,
)
from policies.models import DocumentSnapshot, Policy, PolicyRelation
from rest_framework.test import APIClient


@pytest.fixture
def candidate(db, settings, monkeypatch):
    monkeypatch.setattr(repair, "enabled", lambda: True)
    settings.KNOWLEDGE_LLM_ENABLED = False
    settings.OBSIDIAN_EXPORT_ENABLED = False
    old = Policy.objects.create(title="南宁市污水处理管理办法", body="本办法适用于污水设施运营管理。",
        publication_date=date(2024, 1, 1), source_key="repair-old", content_hash="old",
        status="published", source_grade="L1", source_url="https://www.nanning.gov.cn/old")
    new = Policy.objects.create(title="污水处理管理办法修订通知",
        body="现修订《南宁市污水处理管理办法》，加强污水设施管理。",
        publication_date=date(2026, 1, 1), source_key="repair-new", content_hash="new",
        status="published", source_grade="L1", source_url="https://www.nanning.gov.cn/new")
    scan = KnowledgeRelationScan.objects.create(anchor_policy=new, anchor_version=1,
        candidate_versions={str(old.pk): 1}, input_hash="previous", status="succeeded")
    return RelationReviewCandidate.objects.create(scan=scan, proposal_hash="candidate",
        from_policy=old, to_policy=new, evidence_policy=new, proposed_kind="revises",
        evidence_quote="改写的原文无法定位", rejection_reason="时间顺序不符，请核对关系方向与文号。",
        source_versions={str(old.pk): 1, str(new.pk): 1})


def good_output(payload):
    doc = next(d for d in payload["documents"] if "修订通知" in d["title"])
    return repair.RepairOutput(relations=[relations.PairProposal(
        direction="A_to_B" if doc["role"] == "A" else "B_to_A", relation="revises",
        evidence_excerpt_id=doc["excerpts"][0]["id"], confidence=0.9, reason="新文件明确修订旧办法。")],
        assessment="已从通知原文重新定位修订依据并核对方向。")


def empty_output(*args, **kwargs):
    return repair.RepairOutput(relations=[], assessment="当前读取材料没有明确指向另一文件的直接关系依据。")


def test_repair_corrects_direction_and_keeps_refresh_separate(candidate, monkeypatch):
    monkeypatch.setattr(repair, "model_json", lambda _, payload, *a, **kw: good_output(payload))
    job, _ = repair.enqueue(candidate)
    repair.process(job.pk)
    job.refresh_from_db()
    candidate.refresh_from_db()
    relation = PolicyRelation.objects.get()
    assert relation.from_policy_id == candidate.to_policy_id
    assert relation.evidence_quote in candidate.to_policy.body
    assert job.outcome == "valid_relation" and job.result["new_relations"] == 1
    assert job.result["page_refresh"] == "pending"
    assert candidate.status == "superseded"
    repair.process(job.pk)
    assert PolicyRelation.objects.count() == 1


def test_identical_completed_input_is_cached_and_retains_manual_review(candidate, monkeypatch):
    calls = []
    def model(*args, **kwargs):
        calls.append(True)
        return empty_output()
    monkeypatch.setattr(repair, "model_json", model)
    first, _ = repair.enqueue(candidate)
    repair.process(first.pk)
    second, created = repair.enqueue(candidate)
    repair.process(second.pk)
    candidate.refresh_from_db()
    assert not created and first.pk == second.pk and len(calls) == 1
    assert second.outcome == "no_relation" and candidate.status == "pending"


def test_partial_or_missing_attachment_is_not_negative_confirmation(candidate, monkeypatch):
    DocumentSnapshot.objects.create(policy=candidate.to_policy, url="https://www.nanning.gov.cn/a.pdf",
        sha256="attachment", object_key="test", content_type="application/pdf", size_bytes=10, parse_status="failed")
    monkeypatch.setattr(repair, "model_json", empty_output)
    job, _ = repair.enqueue(candidate)
    repair.process(job.pk)
    job.refresh_from_db()
    assert job.outcome == "needs_review"
    assert any(c["readiness"]["status"] == "incomplete" for c in job.result["coverage"])


@pytest.mark.parametrize("change", ["cancel", "version", "human", "permission"])
def test_late_results_cannot_override_changes(candidate, monkeypatch, change):
    user = get_user_model().objects.create_superuser("repair-operator")
    job, _ = repair.enqueue(candidate, user)
    def model(_, payload, *args, **kwargs):
        if change == "cancel":
            repair.stop(job)
        elif change == "version":
            Policy.objects.filter(pk=candidate.to_policy_id).update(version=2)
        elif change == "human":
            RelationReviewCandidate.objects.filter(pk=candidate.pk).update(status="rejected", reviewed_by=user)
        else:
            get_user_model().objects.filter(pk=user.pk).update(is_active=False)
        return good_output(payload)
    monkeypatch.setattr(repair, "model_json", model)
    repair.process(job.pk)
    job.refresh_from_db()
    assert job.status == ("cancelled" if change == "cancel" else "failed")
    assert not PolicyRelation.objects.exists()


def test_failed_save_reuses_model_checkpoint_on_retry(candidate, monkeypatch):
    calls = []
    def model(*args, **kwargs):
        calls.append(True)
        return empty_output()
    original = repair._persist
    monkeypatch.setattr(repair, "model_json", model)
    monkeypatch.setattr(repair, "_persist", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("storage")))
    job, _ = repair.enqueue(candidate)
    repair.process(job.pk)
    job.refresh_from_db()
    assert job.status == "failed" and job.result["model_output"]
    monkeypatch.setattr(repair, "_persist", original)
    same, _ = repair.enqueue(candidate)
    repair.process(same.pk)
    same.refresh_from_db()
    assert same.status == "succeeded" and len(calls) == 1


def test_invalid_excerpt_is_not_accepted(candidate, monkeypatch):
    def model(_, payload, *args, **kwargs):
        output = good_output(payload)
        output.relations[0].evidence_excerpt_id = "background-0"
        return output
    monkeypatch.setattr(repair, "model_json", model)
    job, _ = repair.enqueue(candidate)
    repair.process(job.pk)
    job.refresh_from_db()
    assert job.outcome == "needs_review"
    assert "未找到" in job.message and not PolicyRelation.objects.exists()
    assert RelationReviewCandidate.objects.count() == 1


def test_long_text_reads_relevant_attachment_and_quotes_stay_bounded(candidate):
    new = candidate.to_policy
    new.body = "其他事项。" * 8000 + "\n附件：修订说明\n" + new.body
    new.save()
    _, payload, mapping, coverage, _ = repair.prepare(candidate)
    assert any("修订说明" in quote for _, quote in mapping.values())
    assert all(len(quote) <= 2200 for _, quote in mapping.values())
    assert any(not c["full"] for c in coverage)
    assert sum(len(q) for _, q in mapping.values()) <= 22000
    output = good_output(payload)
    assert output.relations
    assert any("方向" in a for a in payload["actions"])


def test_human_rejection_blocks_repair_and_later_regular_scan(candidate):
    user = get_user_model().objects.create_superuser("human-reviewer")
    candidate.status, candidate.reviewed_by = "rejected", user
    candidate.save()
    with pytest.raises(ValueError):
        repair.enqueue(candidate)
    group, payload, mapping, _, _ = repair.prepare(candidate)
    resolved = relations.resolve_pair_output(group, good_output(payload), mapping)
    result = relations._persist(group, "later-scan", resolved)
    assert result["preserved_human_relations"] == 1
    assert not PolicyRelation.objects.exists()


def test_management_api_permissions_queue_cache_and_statistics(candidate, monkeypatch):
    client = APIClient()
    path = f"/api/v1/admin/relation-review-candidates/{candidate.pk}/repair"
    client.force_authenticate(get_user_model().objects.create_user("customer"))
    assert client.post(path).status_code == 403
    client.force_authenticate(get_user_model().objects.create_superuser("admin"))
    assert client.post(path).status_code == 202
    assert client.post(path).status_code == 202
    assert RelationRepair.objects.count() == 1
    monkeypatch.setattr(repair, "model_json", empty_output)
    repair.process(RelationRepair.objects.get().pk)
    result = client.get("/api/v1/admin/relation-review-candidates/repair-stats?status=all").json()
    assert result["no_relation"] == 1 and result.get("failed", 0) == 0
    detail = client.get(f"/api/v1/admin/relation-review-candidates/{candidate.pk}").json()
    assert detail["repair"]["outcome"] == "no_relation"
    assert "model_output" not in detail["repair"]["result"]


def test_targeted_refresh_does_not_scan_relations_or_archive_unrelated_pages(candidate, monkeypatch):
    unrelated = KnowledgePage.objects.create(key="other", slug="other", page_type="topic", title="其他专题")
    monkeypatch.setattr(services, "audit_relations", lambda: pytest.fail("must not scan library"))
    result = services.sync_affected([candidate.from_policy_id, candidate.to_policy_id])
    unrelated.refresh_from_db()
    assert unrelated.status == "published"
    assert result["revisions_created"] >= 2 and not result["failed_pages"]
    assert services.sync_affected([candidate.from_policy_id])["revisions_created"] == 0


def test_model_failures_have_finite_retry_budget(candidate, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("connection failure")
    monkeypatch.setattr(repair, "model_json", unavailable)
    for _ in range(5):
        job, _ = repair.enqueue(candidate)
        repair.process(job.pk)
    job.refresh_from_db()
    assert job.status == "failed" and job.attempts == 3
    assert not PolicyRelation.objects.exists()


def test_supplement_never_removes_existing_verified_relationship(candidate, monkeypatch):
    existing = PolicyRelation.objects.create(from_policy=candidate.to_policy, to_policy=candidate.from_policy,
        kind="revises", evidence_policy=candidate.to_policy, evidence_version=1,
        evidence_quote=candidate.to_policy.body, verification_status="verified",
        discovery={"method": "wiki_llm", "scan_anchor_id": str(candidate.from_policy_id)})
    monkeypatch.setattr(repair, "model_json", empty_output)
    job, _ = repair.enqueue(candidate)
    repair.process(job.pk)
    assert PolicyRelation.objects.filter(pk=existing.pk, verification_status="verified").exists()


def test_automatic_repair_switch_does_not_invalidate_analysis_cache(candidate, monkeypatch):
    original = relations.get_config
    rules = original("wiki_relations")
    monkeypatch.setattr(relations, "get_config", lambda key: {**rules, "automatic_repair": False})
    before = relations.relation_runtime_signature()
    monkeypatch.setattr(relations, "get_config", lambda key: {**rules, "automatic_repair": True})
    assert relations.relation_runtime_signature() == before
