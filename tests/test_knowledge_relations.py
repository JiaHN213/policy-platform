from datetime import date

import pytest
from django.contrib.auth import get_user_model
from knowledge import relations as wiki_relations
from knowledge.models import KnowledgeRelationScan, RelationReviewCandidate
from policies.models import Policy, PolicyEnrichment, PolicyRelation
from rest_framework.test import APIClient


def pair_output(kind, valid=True):
    return wiki_relations.PairAuditOutput(
        relations=[
            wiki_relations.PairProposal(
                direction="A_to_B",
                relation=kind,
                evidence_excerpt_id="A-0" if valid else "missing",
                confidence=0.9,
                reason="根据给定原文判断文件关系。",
            )
        ]
    )


def make_policy(key, title, body, published, **overrides):
    values = {
        "title": title,
        "body": body,
        "issuer": "南宁市人民政府",
        "publication_date": published,
        "source_key": key,
        "content_hash": key,
        "source_grade": "L1",
        "status": "published",
        "geographic_level": "city",
        "province": "广西壮族自治区",
        "city": "南宁市",
        "document_type": "policy",
        "business_domains": ["urban_sewage"],
        "structured_keywords": [{"term": "污水处理", "category": "industry"}],
        "source_url": f"https://www.nanning.gov.cn/{key}.html",
    }
    values.update(overrides)
    return Policy.objects.create(**values)


@pytest.fixture
def relation_catalog(db, settings):
    settings.AI_BASE_URL = "http://localhost:11434/v1"
    settings.AI_API_KEY = "ollama"
    settings.AI_MODEL = "qwen3.5:4b"
    settings.KNOWLEDGE_LLM_ENABLED = True
    settings.KNOWLEDGE_RELATION_AUDIT_ENABLED = True
    old = make_policy(
        "old-rule",
        "南宁市污水处理管理办法",
        "本办法规定污水处理设施建设和运营要求。",
        date(2024, 1, 1),
        validity_status="effective",
        validity_evidence="本办法规定污水处理设施建设和运营要求。",
    )
    new = make_policy(
        "new-rule",
        "南宁市污水处理管理办法修订通知",
        "现修订《南宁市污水处理管理办法》，原办法同时停止执行。",
        date(2026, 1, 1),
    )
    return old, new


@pytest.mark.django_db
def test_wiki_llm_builds_relation_once_and_derives_validity(relation_catalog, monkeypatch):
    old, new = relation_catalog
    calls = []

    def model(instruction, data, schema, **kwargs):
        calls.append(data["anchor_policy_id"])
        return pair_output("revises")

    monkeypatch.setattr(wiki_relations, "model_json", model)
    first = wiki_relations.audit_relations()
    relation = PolicyRelation.objects.get()
    old.refresh_from_db()
    assert first["accepted"] == 1
    assert relation.discovery["method"] == "wiki_llm"
    assert relation.verification_status == "verified"
    assert old.validity_status == "replaced"
    assert old.scope_evidence["wiki_validity"]["relation_id"] == str(relation.pk)

    second = wiki_relations.audit_relations()
    assert len(calls) == 1
    assert second["scanned"] == 0
    assert KnowledgeRelationScan.objects.filter(status="succeeded").count() == 1


@pytest.mark.django_db
def test_wiki_rescan_removes_stale_auto_relation_and_restores_validity(
    relation_catalog, monkeypatch
):
    old, new = relation_catalog

    def relation_model(*args, **kwargs):
        return pair_output("replaces")

    monkeypatch.setattr(wiki_relations, "model_json", relation_model)
    wiki_relations.audit_relations()
    assert PolicyRelation.objects.exists()

    new.version = 2
    new.content_hash = "new-rule-v2"
    new.save(update_fields=["version", "content_hash", "updated_at"])
    monkeypatch.setattr(
        wiki_relations,
        "model_json",
        lambda *args, **kwargs: wiki_relations.PairAuditOutput(relations=[]),
    )
    result = wiki_relations.audit_relations()
    old.refresh_from_db()
    assert result["validity_updates"] == 1
    assert not PolicyRelation.objects.exists()
    assert old.validity_status == "effective"
    assert "wiki_validity" not in old.scope_evidence


@pytest.mark.django_db
def test_invalid_wiki_evidence_is_rejected_and_human_relation_is_preserved(
    relation_catalog, monkeypatch
):
    old, new = relation_catalog
    operator = get_user_model().objects.create_superuser("relation-editor")
    human = PolicyRelation.objects.create(
        from_policy=new,
        to_policy=old,
        kind="revises",
        evidence_policy=new,
        evidence_version=new.version,
        evidence_quote="现修订《南宁市污水处理管理办法》",
        verification_status="rejected",
        verified_by=operator,
        discovery={"method": "human_override", "locked": True},
    )

    monkeypatch.setattr(
        wiki_relations,
        "model_json",
        lambda *args, **kwargs: pair_output("revises", valid=False),
    )
    result = wiki_relations.audit_relations()
    human.refresh_from_db()
    assert result["invalid_proposals"] == 1
    assert PolicyRelation.objects.count() == 1
    assert human.verification_status == "rejected"
    assert human.discovery["locked"] is True


@pytest.mark.django_db
def test_legacy_ai_relations_are_removed_but_human_rejections_are_kept(relation_catalog):
    old, new = relation_catalog
    automatic = PolicyRelation.objects.create(
        from_policy=new,
        to_policy=old,
        kind="implements",
        evidence_policy=new,
        evidence_version=1,
        evidence_quote="现修订《南宁市污水处理管理办法》",
        verification_status="verified",
        discovery={"method": "ai"},
    )
    rejected = PolicyRelation.objects.create(
        from_policy=new,
        to_policy=old,
        kind="revises",
        evidence_policy=new,
        evidence_version=1,
        evidence_quote="现修订《南宁市污水处理管理办法》",
        verification_status="rejected",
        discovery={"method": "ai"},
    )
    enrichment = PolicyEnrichment.objects.create(
        policy=new,
        policy_version=1,
        result={"relations": {"edges": [str(automatic.pk)]}, "review": {}},
    )

    result = wiki_relations.retire_legacy_ai_relations()
    rejected.refresh_from_db()
    enrichment.refresh_from_db()
    assert result == {"removed": 1, "preserved_human": 1}
    assert not PolicyRelation.objects.filter(pk=automatic.pk).exists()
    assert rejected.discovery["method"] == "human_override"
    # Runtime cleanup concerns relation rows; the data migration removes historical job payloads.
    assert "relations" in enrichment.result


@pytest.mark.django_db
def test_semantic_gate_rejects_wrong_time_direction_and_missing_action_word(
    relation_catalog,
):
    old, new = relation_catalog
    wrong_direction = wiki_relations._semantic_validation(
        old,
        new,
        old,
        f"依据《{new.title}》执行",
        "implements",
    )
    wrong_kind = wiki_relations._semantic_validation(
        new,
        old,
        new,
        f"参照《{old.title}》办理",
        "application",
    )

    assert "时间顺序" in wrong_direction
    assert "动作词" in wrong_kind


@pytest.mark.django_db
def test_rejected_wiki_relation_can_be_corrected_and_approved(relation_catalog, monkeypatch):
    old, new = relation_catalog
    admin = get_user_model().objects.create_superuser("relation-reviewer")
    quote = "现修订《南宁市污水处理管理办法》"
    monkeypatch.setattr(
        wiki_relations,
        "model_json",
        lambda *args, **kwargs: pair_output("implements"),
    )
    result = wiki_relations.audit_relations()
    candidate = RelationReviewCandidate.objects.get(status="pending")
    assert result["invalid_proposals"] == 1

    client = APIClient()
    client.force_authenticate(admin)
    response = client.post(
        f"/api/v1/admin/relation-review-candidates/{candidate.pk}/approve",
        {
            "from_policy": str(new.pk),
            "to_policy": str(old.pk),
            "kind": "revises",
            "evidence_policy": str(new.pk),
            "evidence_quote": quote,
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    relation = PolicyRelation.objects.get(kind="revises")
    candidate.refresh_from_db()
    assert relation.verified_by == admin
    assert relation.discovery["locked"] is True
    assert candidate.status == "approved"
