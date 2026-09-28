from copy import deepcopy
from datetime import date

import pytest
from core.business_config import get_config
from django.core.management import call_command
from knowledge import relations as r
from knowledge.models import RelationReviewCandidate
from policies.models import Policy, PolicyRelation
from pydantic import ValidationError


def policy(key, body, day=1):
    return Policy.objects.create(
        title=f"水务项目管理{key}办法",
        body=body,
        publication_date=date(2026, 1, day),
        source_key=key,
        content_hash=key,
        source_grade="L1",
        status="published",
        source_url=f"https://www.gov.cn/{key}",
    )


def proposal(a, b, quote, kind="revises"):
    return r.RelationProposal(
        from_policy_id=str(a.pk),
        to_policy_id=str(b.pk),
        relation=kind,
        evidence_policy_id=str(a.pk),
        evidence_quote=quote,
        confidence=0.9,
        reason="依据原文确认。",
    )


def test_whitespace_alignment_preserves_source_and_does_not_drop_punctuation():
    body = "现对《水务管理办法》\n进行 调整。"
    assert r.aligned_quote("现对《水务管理办法》进行调整。", body) == body
    assert not r.aligned_quote("现对水务管理办法进行调整。", body)
    assert not r.aligned_quote("现对《水务管理办法》...调整。", body)
    assert not r.aligned_quote("现对《水务管理办法》不进行调整。", body)
    assert not r.aligned_quote("水务政策调整", "水务 政策调整。水务政策 调整。")


@pytest.mark.django_db
def test_persist_whitespace_quote_as_real_span_and_explain_historical_rejection():
    old = policy("旧", "原办法。")
    body = f"现修订《{old.title}》，\n 原办法同时停止执行。"
    new = policy("新", body, 2)
    group = r.CandidateGroup(new, [old], {str(old.pk): 100})
    p = proposal(new, old, body.replace("\n ", ""))
    result = r._persist(group, "good-whitespace", r.RelationAuditOutput(relations=[p]))
    assert len(result["accepted_relation_ids"]) == 1
    saved = PolicyRelation.objects.get()
    assert saved.evidence_quote == body
    assert saved.discovery["quote_alignment"] == "whitespace"
    broken = proposal(new, old, f"现修订《{old.title}》...停止执行。")
    r._persist(group, "bad-fragment", r.RelationAuditOutput(relations=[broken]))
    candidate = RelationReviewCandidate.objects.get()
    assert "不是连续原文" in candidate.rejection_reason
    candidate.rejection_reason = "关系方向、文件编号或原文证据未通过确定性校验"
    candidate.save()
    relation_count = PolicyRelation.objects.count()
    call_command("explain_relation_candidates")
    candidate.refresh_from_db()
    assert candidate.status == "pending" and "不是连续原文" in candidate.rejection_reason
    assert candidate.evidence_quote == broken.evidence_quote
    assert PolicyRelation.objects.count() == relation_count


@pytest.mark.django_db
def test_adjustment_requires_explicit_target_not_shared_subject():
    old = policy("旧", "原办法。")
    new = policy("新", f"决定对《{old.title}》中部分项目进行调整。", 2)
    assert not r._semantic_validation(new, old, new, new.body, "revises")
    assert "未确认其直接作用" in r._semantic_validation(
        new, old, new, f"依据《{old.title}》，对其他项目进行调整。", "revises"
    )
    assert "主题相似" in r._semantic_validation(
        new, old, new, "本年度实施污水处理项目。", "replaces"
    )
    reverse = r._semantic_validation(old, new, new, f"《{old.title}》同时废止。", "repeals")
    assert "时间顺序" in reverse and old.title in reverse and new.title in reverse


@pytest.mark.django_db
def test_pairs_resume_individually_with_bounded_source_excerpts(monkeypatch):
    old = policy("旧", "旧政策正文" * 5000)
    another = policy("其他", "另一个政策正文。")
    new = policy("新", f"现修订《{old.title}》，原办法停止执行。", 2)
    group = r.CandidateGroup(new, [old, another], {str(old.pk): 100, str(another.pk): 90})
    monkeypatch.setattr(r, "enabled", lambda: True)
    monkeypatch.setattr(r, "candidate_groups", lambda: [group])
    calls = []

    def model(instruction, data, schema, **kwargs):
        assert schema is r.PairAuditOutput
        assert len(data["documents"]) == 2
        assert sum(len(e["text"]) for d in data["documents"] for e in d["excerpts"]) <= 12000
        for doc in data["documents"]:
            source = new if doc["role"] == "A" else old if doc["title"] == old.title else another
            assert all(e["text"] in source.body for e in doc["excerpts"])
        calls.append(data)
        return r.PairAuditOutput(relations=[])

    monkeypatch.setattr(r, "model_json", model)
    first = r.audit_relations(max_model_scans=1)
    second = r.audit_relations(max_model_scans=1)
    third = r.audit_relations(max_model_scans=1)
    assert first["pending"] == 1 and second["pending"] == third["pending"] == 0
    assert third["model_scans"] == 0 and len(calls) == 2


@pytest.mark.django_db
def test_fragment_identity_and_config_changes_are_not_silently_reused(monkeypatch):
    old = policy("旧", "旧政策正文。")
    new = policy("新", f"现修订《{old.title}》。", 2)
    group = r.CandidateGroup(new, [old], {str(old.pk): 100})
    payload, mapping = r.pair_payload(group)
    output = r.PairAuditOutput(
        relations=[
            r.PairProposal(
                direction="B_to_A",
                relation="repeals",
                evidence_excerpt_id=payload["documents"][0]["excerpts"][0]["id"],
                confidence=0.9,
                reason="原文依据",
            )
        ]
    )
    resolved = r.resolve_pair_output(group, output, mapping).relations[0]
    assert resolved.from_policy_id == str(old.pk) and resolved.evidence_quote == new.body
    assert resolved.evidence_policy_id == str(new.pk)
    initial = r._input_hash(group)
    config = deepcopy(get_config("wiki_relations"))
    config["kind_cues"]["revises"].append("重新修订")
    monkeypatch.setattr(r, "get_config", lambda *args, **kwargs: config)
    assert r._input_hash(group) != initial
    with pytest.raises(ValidationError):
        r.PairProposal(
            direction="A_to_B",
            relation="相似",
            evidence_excerpt_id="A-0",
            confidence=1,
            reason="相似",
        )


@pytest.mark.django_db
def test_finalizes_uses_draft_to_formal_direction():
    draft = policy("征求意见稿", "请提出意见。")
    draft.document_type = "draft"
    formal = policy("正式", f"根据《{draft.title}》征求意见情况发布正式版本。", 2)
    assert "征求意见稿A指向正式文件B" in get_config("wiki_relations")["directions"]["finalizes"]
    assert not r._semantic_validation(draft, formal, formal, formal.body, "finalizes")
