from datetime import date, timedelta

import pytest
from core.errors import Conflict
from core.models import AuditRecord
from django.contrib.auth import get_user_model
from django.utils import timezone
from knowledge.models import KnowledgePage, KnowledgePageSource, KnowledgeRevision
from policies.models import Policy, PolicyEnrichment, PolicyRelation
from quality.models import EvaluationRun, EvaluationSample
from quality.services import (
    create_sample,
    evaluate,
    label_sample,
    prediction,
    prediction_hash,
    seed_samples,
)
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient


def policy(index, predicted="NONE"):
    item = Policy.objects.create(
        title=f"水务政策{index}",
        body="支持水务企业申报设备更新项目，给予资金补助。",
        publication_date=date(2026, 1, 1),
        source_url=f"https://www.gov.cn/{index}",
        source_key=f"quality-{index}",
        content_hash=f"hash-{index}",
        status="published",
        source_grade="L1",
        opportunity_level=predicted,
    )
    PolicyEnrichment.objects.create(
        policy=item,
        policy_version=1,
        status="succeeded",
        model="test-model",
        result={
            "review": {"opportunity_level": predicted, "evidence": [{"quote": item.body}]},
            "config_version": "test-config",
        },
    )
    return item


def label(sample, actor, **changes):
    doc = sample.snapshot["documents"][0]
    data = {
        "label_version": sample.label_version,
        "prediction_hash": prediction_hash(prediction(sample)),
        "notes": "根据原文判断正确结果",
        "evidence_supported": True,
        "evidence_policy_id": doc["id"],
        "evidence_quote": doc["body"],
        **changes,
    }
    return label_sample(sample.pk, actor, data)


@pytest.mark.django_db
def test_no_gold_never_reports_perfect_quality_or_modifies_policies():
    actor = get_user_model().objects.create_superuser("quality-admin")
    item = policy(1, "FORMAL_OPPORTUNITY")
    original = item.body
    assert seed_samples(actor, 6) == 1
    sample = EvaluationSample.objects.get()
    assert sample.status == "pending" and sample.gold == {} and sample.labeled_by is None
    run = evaluate(actor)
    assert run.status == "no_labels"
    assert all(metrics["accuracy"] is None for metrics in run.metrics.values())
    item.refresh_from_db()
    assert item.body == original and item.opportunity_level == "FORMAL_OPPORTUNITY"


@pytest.mark.django_db
def test_opportunity_confusion_counts_include_false_negatives_and_false_positives():
    actor = get_user_model().objects.create_superuser("quality-admin")
    cases = [
        ("FORMAL_OPPORTUNITY", "FORMAL_OPPORTUNITY"),
        ("NONE", "FORMAL_OPPORTUNITY"),
        ("FORMAL_OPPORTUNITY", "NONE"),
        ("SUPPORT_SIGNAL", "SUPPORT_SIGNAL"),
    ]
    for index, (predicted, gold) in enumerate(cases):
        sample, _ = create_sample(kind="opportunity", policy=policy(index, predicted))
        label(sample, actor, opportunity_level=gold)
    run = evaluate(actor)
    metrics = run.metrics["opportunity"]
    assert [metrics[key] for key in ("tp", "fp", "fn", "tn")] == [1, 1, 1, 1]
    assert (
        metrics["accuracy"]
        == metrics["precision"]
        == metrics["recall"]
        == metrics["miss_rate"]
        == 0.5
    )
    assert run.results.count() == 4
    assert run.results.first().prediction["model"] == "test-model"


@pytest.mark.django_db
def test_labels_require_evidence_preserve_history_and_exclude_changed_sources():
    actor = get_user_model().objects.create_superuser("quality-admin")
    item = policy(1)
    sample, _ = create_sample(kind="opportunity", policy=item)
    with pytest.raises(ValidationError):
        label(
            sample, actor, opportunity_level="FORMAL_OPPORTUNITY", evidence_quote="不存在的原文依据"
        )
    labeled = label(sample, actor, opportunity_level="FORMAL_OPPORTUNITY")
    with pytest.raises(Conflict):
        label(sample, actor, opportunity_level="NONE")
    first = evaluate(actor)
    label(labeled, actor, opportunity_level="NONE")
    assert first.results.get().gold["opportunity_level"] == "FORMAL_OPPORTUNITY"
    assert AuditRecord.objects.filter(action="quality.sample.labeled").count() == 2
    item.version += 1
    item.save()
    run = evaluate(actor)
    assert run.metrics["opportunity"]["stale"] == 1
    assert run.metrics["opportunity"]["evaluated"] == 0
    assert run.metrics["opportunity"]["accuracy"] is None


@pytest.mark.django_db
def test_changed_ai_output_does_not_reuse_human_evidence_rating():
    actor = get_user_model().objects.create_superuser("quality-admin")
    item = policy(1, "FORMAL_OPPORTUNITY")
    sample, _ = create_sample(kind="opportunity", policy=item)
    label(sample, actor, opportunity_level="FORMAL_OPPORTUNITY")
    job = item.enrichments.get()
    job.result["review"]["evidence"] = [{"quote": "模型添加的无依据文字"}]
    job.save()
    run = evaluate(actor)
    metrics = run.metrics["opportunity"]
    assert metrics["accuracy"] == 1
    assert metrics["citation_grounding"] == 0
    assert metrics["human_evidence_support"] is None


@pytest.mark.django_db
def test_relation_direction_missing_edges_and_manual_overrides():
    actor = get_user_model().objects.create_superuser("quality-admin")
    a, b = policy(1), policy(2)
    relation = PolicyRelation.objects.create(
        from_policy=a,
        to_policy=b,
        kind="implements",
        verification_status="verified",
        evidence_policy=a,
        evidence_version=1,
        evidence_quote=a.body,
        discovery={"method": "wiki_llm", "model": "wiki-test"},
    )
    forward, _ = create_sample(
        kind="relation", policy=a, related_policy=b, relation_kind="implements"
    )
    reverse, _ = create_sample(
        kind="relation", policy=b, related_policy=a, relation_kind="implements"
    )
    label(forward, actor, verdict=True)
    label(reverse, actor, verdict=True)
    metrics = evaluate(actor).metrics["relation"]
    assert metrics["tp"] == metrics["fn"] == 1 and metrics["recall"] == 0.5
    relation.discovery = {"method": "human_override"}
    relation.save()
    assert evaluate(actor).metrics["relation"]["unavailable"] == 1


@pytest.mark.django_db
def test_knowledge_evidence_and_changed_revision():
    actor = get_user_model().objects.create_superuser("quality-admin")
    item = policy(1)
    page = KnowledgePage.objects.create(
        key="topic:test", slug="quality-topic", page_type="topic", title="水务专题"
    )
    revision = KnowledgeRevision.objects.create(
        page=page,
        number=1,
        body="水务专题结论",
        input_hash="abc",
        model="wiki-model",
        citations=[{"policy_id": str(item.pk), "quote": item.body}],
        source_versions={str(item.pk): 1},
    )
    page.current_revision = revision
    page.save()
    KnowledgePageSource.objects.create(page=page, policy=item, policy_version=1)
    sample, _ = create_sample(kind="knowledge", page=page)
    label(sample, actor, verdict=False, evidence_supported=False)
    metrics = evaluate(actor).metrics["knowledge"]
    assert (
        metrics["fp"] == 1
        and metrics["citation_grounding"] == 1
        and metrics["human_evidence_support"] == 0
    )
    page.current_revision = KnowledgeRevision.objects.create(
        page=page, number=2, body="更新", input_hash="def"
    )
    page.save()
    assert evaluate(actor).metrics["knowledge"]["stale"] == 1


@pytest.mark.django_db
def test_quality_permissions_manual_api_and_daily_deduplication():
    admin = get_user_model().objects.create_superuser("quality-admin")
    reader = get_user_model().objects.create_user("quality-reader")
    item = policy(1)
    client = APIClient()
    client.force_authenticate(reader)
    for url in ("samples", "samples/summary", "samples/policies", "runs"):
        assert client.get(f"/api/v1/admin/quality/{url}").status_code == 403
    assert client.post("/api/v1/admin/quality/samples/seed").status_code == 403
    assert client.post("/api/v1/admin/quality/runs/evaluate").status_code == 403
    client.force_authenticate(admin)
    created = client.post(
        "/api/v1/admin/quality/samples/add",
        {"kind": "opportunity", "policy_id": str(item.pk)},
        format="json",
    )
    assert created.status_code == 201
    sample_id = created.data["sample"]["id"]
    detail = client.get(f"/api/v1/admin/quality/samples/{sample_id}").data
    assert detail["snapshot"]["documents"][0]["body"] == item.body
    response = client.post(
        f"/api/v1/admin/quality/samples/{sample_id}/label",
        {
            "label_version": 0,
            "prediction_hash": detail["prediction_hash"],
            "notes": "原文没有政策机会",
            "opportunity_level": "NONE",
            "evidence_supported": False,
        },
        format="json",
    )
    assert response.status_code == 200
    run = evaluate(scheduled=True)
    assert evaluate(scheduled=True).pk == run.pk
    EvaluationRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(hours=25))
    assert evaluate(scheduled=True).pk != run.pk
    assert (
        client.get(f"/api/v1/admin/quality/runs/{run.pk}/results?errors_only=true").status_code
        == 200
    )
