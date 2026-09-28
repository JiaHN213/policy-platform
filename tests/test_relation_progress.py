from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from knowledge import progress as p
from knowledge.models import KnowledgeBuild, KnowledgeRelationScan, RelationReviewCandidate
from knowledge.relations import CandidateGroup, _input_hash, relation_runtime_signature
from policies.models import Policy
from rest_framework.test import APIClient


@pytest.fixture
def catalog(db):
    cache.clear()
    first = Policy.objects.create(title="供水设施办法", body="支持供水设施建设。", source_key="progress-a",
                                  content_hash="a", publication_date=date(2026, 1, 1),
                                  status="published", source_grade="L1")
    second = Policy.objects.create(title="供水设施申报通知", body="依据《供水设施办法》开展申报。", source_key="progress-b",
                                   content_hash="b", publication_date=date(2026, 2, 1),
                                   status="published", source_grade="L1")
    yield first, second
    cache.clear()


def scan(first, second, digest, status="succeeded", result=None):
    return KnowledgeRelationScan.objects.create(anchor_policy=second, anchor_version=second.version,
        candidate_versions={str(first.pk): first.version}, input_hash=digest, status=status,
        result=result or {}, prompt_version="test")


def candidate(first, second, record, key):
    return RelationReviewCandidate.objects.create(scan=record, proposal_hash=key,
        from_policy=second, to_policy=first, proposed_kind="implements", rejection_reason="依据待核实")


@pytest.mark.django_db
def test_progress_counts_empty_results_failures_and_historical_candidates(catalog, monkeypatch):
    a, b = catalog
    monkeypatch.setattr(p, "relation_manifest", lambda: {"hashes": ["ok", "failed", "waiting"], "policy_count": 2})
    current = scan(a, b, "ok")
    scan(a, b, "failed", "failed")
    old = scan(a, b, "old")
    candidate(a, b, current, "current")
    candidate(a, b, old, "historical")
    KnowledgeBuild.objects.create(status="running", lease_until=timezone.now() + timedelta(minutes=30))
    result = p.relation_progress()
    assert (result["total"], result["completed"], result["waiting"], result["failed"]) == (3, 1, 1, 1)
    assert result["percent"] == 33.3
    assert result["no_relation_pairs"] == 1
    assert result["review"]["current"]["pending"] == result["review"]["historical"]["pending"] == 1
    assert result["review"]["all"]["pending"] == 2
    client = APIClient()
    assert client.get("/api/v1/admin/knowledge/builds/progress").status_code in {401, 403}
    client.force_authenticate(get_user_model().objects.create_superuser("progress-admin"))
    assert client.get("/api/v1/admin/knowledge/builds/progress").data["completed"] == 1
    for scope in ("current", "historical", "all"):
        response = client.get(f"/api/v1/admin/relation-review-candidates?scope={scope}&status=pending")
        assert response.status_code == 200
        assert response.data["count"] == result["review"][scope]["pending"]
    assert client.get("/api/v1/admin/relation-review-candidates?scope=invalid").status_code == 400


@pytest.mark.django_db
def test_manifest_matches_worker_hash_and_invalidates_on_policy_change(catalog):
    a, b = catalog
    group = CandidateGroup(b, [a], {str(a.pk): 1})
    digest = _input_hash(group)
    assert digest == _input_hash(group, runtime=relation_runtime_signature())
    assert p.relation_manifest()["hashes"] == [digest]
    scan(a, b, digest)
    assert p.relation_progress()["completed"] == 1
    b.version += 1
    b.save(update_fields=["version"])
    assert p.relation_progress()["completed"] == 0


@pytest.mark.django_db
def test_zero_candidates_and_finalizing_are_not_false_completion(catalog, monkeypatch):
    a, b = catalog
    manifest = {"hashes": [], "policy_count": 2}
    monkeypatch.setattr(p, "relation_manifest", lambda: manifest)
    assert p.relation_progress()["status"] == "empty"
    assert p.relation_progress()["percent"] == 0
    manifest["hashes"] = ["done"]
    scan(a, b, "done")
    build = KnowledgeBuild.objects.create(status="running", lease_until=timezone.now() + timedelta(minutes=30))
    assert p.relation_progress()["status"] == "finalizing"
    build.status = "succeeded"
    build.save()
    assert p.relation_progress()["status"] == "completed"
