"""Score saved system outputs against explicit human labels; never change policies."""

import hashlib
import json
import random
import uuid
from datetime import timedelta

from core.business_config import config_version
from core.errors import Conflict
from core.models import AuditRecord
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone
from knowledge.models import KnowledgePage, RelationReviewCandidate
from policies.models import Policy, PolicyRelation
from policies.taxonomy import OpportunityLevel, RelationKind
from rest_framework.exceptions import ValidationError

from .models import EvaluationResult, EvaluationRun, EvaluationSample


def document(policy):
    return {
        "id": str(policy.pk),
        "version": policy.version,
        "content_hash": policy.content_hash,
        "title": policy.title,
        "body": policy.body,
        "source_url": policy.source_url,
        "source_grade": policy.source_grade,
        "attachments": list(policy.snapshots.values("url", "parse_status")),
    }


def prediction_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def create_sample(
    *, kind, policy=None, related_policy=None, page=None, relation_kind="", origin="manual"
):
    if kind not in EvaluationSample.Kind.values:
        raise ValidationError("请选择有效的评测类型。")
    if (
        (kind == "opportunity" and (related_policy or page or relation_kind))
        or (kind == "relation" and page)
        or (kind == "knowledge" and (policy or related_policy or relation_kind))
    ):
        raise ValidationError("所选资料与评测类型不一致，请重新选择。")
    if kind == "knowledge":
        if not page or not page.current_revision:
            raise ValidationError("请选择已有修订的知识页。")
        documents = [
            document(source.policy) for source in page.page_sources.select_related("policy")
        ]
        snapshot = {
            "documents": documents,
            "page_revision": str(page.current_revision_id),
            "page_body": page.current_revision.body,
            "citations": page.current_revision.citations,
            "model": page.current_revision.model,
            "prompt_version": page.current_revision.prompt_version,
        }
        title = page.title
    else:
        if not policy or policy.is_demo:
            raise ValidationError("请选择真实政策文件。")
        documents = [document(policy)]
        title = policy.title
        if kind == "relation":
            if (
                not related_policy
                or related_policy.is_demo
                or related_policy.pk == policy.pk
                or relation_kind not in RelationKind.values
            ):
                raise ValidationError("关系评测需要两份不同的真实政策，以及明确的关系类型和方向。")
            documents.append(document(related_policy))
            title = f"{policy.title} → {dict(RelationKind.choices)[relation_kind]} → {related_policy.title}"
        snapshot = {"documents": documents}
    identity = {
        "kind": kind,
        "documents": [{k: d[k] for k in ("id", "version", "content_hash")} for d in documents],
        "page": str(page.pk) if page else None,
        "revision": snapshot.get("page_revision"),
        "relation_kind": relation_kind,
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return EvaluationSample.objects.get_or_create(
        key=key,
        defaults={
            "kind": kind,
            "policy": policy,
            "related_policy": related_policy,
            "page": page,
            "relation_kind": relation_kind,
            "title": title[:1000],
            "snapshot": snapshot,
            "origin": origin,
        },
    )


@transaction.atomic
def seed_samples(actor, limit=30):
    """Stratified drafts only; AI outputs never become gold labels."""
    rng = random.Random(20260927)
    added = 0
    buckets = []
    for level in OpportunityLevel.values:
        candidates = list(
            Policy.objects.filter(
                is_demo=False,
                opportunity_level=level,
                enrichments__status="succeeded",
                enrichments__policy_version=F("version"),
            )
            .distinct()
            .order_by("id")[:1000]
        )
        rng.shuffle(candidates)
        buckets.append(iter(candidates))
    for _ in range(limit):
        for bucket in buckets:
            policy = next(bucket, None)
            if policy and added < limit // 2:
                _, created = create_sample(kind="opportunity", policy=policy, origin="stratified")
                added += int(created)
    for relation in (
        PolicyRelation.objects.filter(
            discovery__method="wiki_llm", from_policy__is_demo=False, to_policy__is_demo=False
        )
        .select_related("from_policy", "to_policy")
        .order_by("id")[: max(2, limit // 6)]
    ):
        if added >= limit - 3:
            break
        _, created = create_sample(
            kind="relation",
            policy=relation.from_policy,
            related_policy=relation.to_policy,
            relation_kind=relation.kind,
            origin="accepted_relation",
        )
        added += int(created)
    for candidate in (
        RelationReviewCandidate.objects.filter(
            proposed_kind__in=RelationKind.values,
            from_policy__is_demo=False,
            to_policy__is_demo=False,
        )
        .select_related("from_policy", "to_policy")
        .order_by("id")[:limit]
    ):
        if added >= limit - 3:
            break
        _, created = create_sample(
            kind="relation",
            policy=candidate.from_policy,
            related_policy=candidate.to_policy,
            relation_kind=candidate.proposed_kind,
            origin="rejected_candidate",
        )
        added += int(created)
    for page in (
        KnowledgePage.objects.filter(
            page_type__in=["chain", "topic", "region"], current_revision__isnull=False
        )
        .select_related("current_revision")
        .order_by("page_type", "id")
    ):
        if added >= limit:
            break
        _, created = create_sample(kind="knowledge", page=page, origin="knowledge_page")
        added += int(created)
    AuditRecord.objects.create(
        actor=actor,
        action="quality.samples.created",
        object_id=uuid.uuid4(),
        details={"created": added},
    )
    return added


def stale_reason(sample):
    for source in sample.snapshot.get("documents", []):
        policy = Policy.objects.filter(pk=source["id"]).first()
        if (
            not policy
            or policy.version != source["version"]
            or policy.content_hash != source["content_hash"]
        ):
            return "政策原文或版本已变化，请从当前资料创建新样本后重新标注。"
    if sample.kind == "knowledge" and (
        not sample.page
        or str(sample.page.current_revision_id) != sample.snapshot.get("page_revision")
    ):
        return "知识页已生成新修订，请重新抽样和标注。"
    return ""


@transaction.atomic
def label_sample(sample_id, actor, data):
    sample = EvaluationSample.objects.select_for_update().get(pk=sample_id)
    if data["label_version"] != sample.label_version:
        raise Conflict("其他人员已经更新标注，请刷新后核对。")
    stale = stale_reason(sample)
    if stale:
        raise ValidationError(stale)
    if sample.status == "retired":
        raise ValidationError("此样本已停用，请重新抽样。")
    current_prediction_hash = prediction_hash(prediction(sample))
    if data["prediction_hash"] != current_prediction_hash:
        raise Conflict("系统输出已经变化，请刷新后核对引用证据再保存标注。")
    gold = {
        "notes": data["notes"],
        "evidence_supported": data["evidence_supported"],
        "evidence_policy_id": str(data.get("evidence_policy_id") or ""),
        "evidence_quote": data.get("evidence_quote", ""),
        "evidence_prediction_hash": current_prediction_hash,
    }
    if sample.kind == "opportunity":
        if data.get("opportunity_level") not in OpportunityLevel.values:
            raise ValidationError("请选择政策机会判断结果。")
        gold["opportunity_level"] = data["opportunity_level"]
        positive = data["opportunity_level"] != "NONE"
    else:
        if data.get("verdict") is None:
            raise ValidationError("请明确选择有无该关系，或知识页是否得到原文支持。")
        gold["verdict"] = data["verdict"]
        positive = data["verdict"]
    quote = gold["evidence_quote"].strip()
    if positive or quote:
        source = next(
            (d for d in sample.snapshot["documents"] if d["id"] == gold["evidence_policy_id"]), None
        )
        if not source or len(quote) < 5 or quote not in source["body"]:
            raise ValidationError("正向结论必须选择证据文件，并逐字引用至少5个字符的原文。")
    previous = sample.gold
    sample.gold = gold
    sample.status = "labeled"
    sample.label_version += 1
    sample.labeled_by = actor
    sample.labeled_at = timezone.now()
    sample.save()
    AuditRecord.objects.create(
        actor=actor,
        action="quality.sample.labeled",
        object_id=sample.pk,
        details={"old": previous, "new": gold, "label_version": sample.label_version},
    )
    return sample


def quote_check(quotes, documents):
    results = [
        bool(
            q.get("quote")
            and any(
                q["quote"] in d["body"]
                for d in documents
                if not q.get("policy_id") or str(q["policy_id"]) == d["id"]
            )
        )
        for q in quotes
    ]
    return {"quotes": len(results), "grounded_quotes": sum(results), "citations": quotes}


def prediction(sample):
    docs = sample.snapshot["documents"]
    if sample.kind == "opportunity":
        job = (
            sample.policy.enrichments.filter(
                policy_version=sample.policy.version, status="succeeded"
            )
            .order_by("-updated_at")
            .first()
        )
        if (
            not job
            or (job.result or {}).get("review", {}).get("opportunity_level")
            not in OpportunityLevel.values
        ):
            return None
        review = job.result["review"]
        return {
            "value": review["opportunity_level"],
            "model": job.model,
            "prompt_version": job.prompt_version,
            "config_version": job.result.get("config_version", ""),
            **quote_check(review.get("evidence", []), docs),
        }
    if sample.kind == "relation":
        relation = (
            PolicyRelation.objects.filter(
                from_policy=sample.policy,
                to_policy=sample.related_policy,
                kind=sample.relation_kind,
            )
            .select_related("evidence_policy")
            .first()
        )
        if relation and (relation.discovery or {}).get("method") != "wiki_llm":
            return None
        valid = bool(
            relation
            and relation.verification_status == "verified"
            and relation.evidence_version == relation.evidence_policy.version
        )
        return {
            "value": valid,
            "model": (relation.discovery or {}).get("model", "") if relation else "",
            **quote_check(
                [{"quote": relation.evidence_quote, "policy_id": str(relation.evidence_policy_id)}]
                if valid
                else [],
                docs,
            ),
        }
    page = sample.page
    revision = page.current_revision
    checked = quote_check(revision.citations, docs)
    valid_sources = bool(docs) and all(
        Policy.objects.filter(
            pk=d["id"], status="published", source_grade__in=Policy.FORMAL_SOURCE_GRADES
        ).exists()
        for d in docs
    )
    return {
        "value": bool(
            page.status == "published"
            and valid_sources
            and checked["quotes"] > 0
            and checked["grounded_quotes"] == checked["quotes"]
        ),
        "model": revision.model,
        "prompt_version": revision.prompt_version,
        **checked,
    }


def ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


@transaction.atomic
def evaluate(actor=None, *, scheduled=False):
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(76241003)")
    latest = EvaluationRun.objects.order_by("-created_at").first()
    if scheduled and (
        not EvaluationSample.objects.filter(status="labeled").exists()
        or (latest and latest.created_at > timezone.now() - timedelta(hours=24))
    ):
        return latest
    run = EvaluationRun.objects.create(requested_by=actor, config_version=config_version())
    metrics = {}
    for kind in EvaluationSample.Kind.values:
        counts = {
            "labeled": 0,
            "evaluated": 0,
            "stale": 0,
            "unavailable": 0,
            "correct": 0,
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "tn": 0,
            "quotes": 0,
            "grounded_quotes": 0,
            "human_evidence_checked": 0,
            "human_evidence_supported": 0,
            "confusion": {},
        }
        for sample in EvaluationSample.objects.filter(kind=kind, status="labeled").select_related(
            "policy", "related_policy", "page__current_revision"
        ):
            counts["labeled"] += 1
            reason = stale_reason(sample)
            predicted = None if reason else prediction(sample)
            status = "stale" if reason else "unavailable" if predicted is None else "evaluated"
            gold_value = (
                sample.gold.get("opportunity_level")
                if kind == "opportunity"
                else sample.gold.get("verdict")
            )
            correct = predicted["value"] == gold_value if status == "evaluated" else None
            if status == "evaluated":
                counts["evaluated"] += 1
                counts["correct"] += int(correct)
                expected_positive = (
                    gold_value == "FORMAL_OPPORTUNITY" if kind == "opportunity" else gold_value
                )
                predicted_positive = (
                    predicted["value"] == "FORMAL_OPPORTUNITY"
                    if kind == "opportunity"
                    else predicted["value"]
                )
                counts[
                    "tp"
                    if expected_positive and predicted_positive
                    else "fn"
                    if expected_positive
                    else "fp"
                    if predicted_positive
                    else "tn"
                ] += 1
                counts["quotes"] += predicted["quotes"]
                counts["grounded_quotes"] += predicted["grounded_quotes"]
                if predicted["quotes"] and sample.gold.get(
                    "evidence_prediction_hash"
                ) == prediction_hash(predicted):
                    counts["human_evidence_checked"] += 1
                    counts["human_evidence_supported"] += int(sample.gold["evidence_supported"])
                key = f"{gold_value} → {predicted['value']}"
                counts["confusion"][key] = counts["confusion"].get(key, 0) + 1
            else:
                counts[status] += 1
            EvaluationResult.objects.create(
                run=run,
                sample=sample,
                label_version=sample.label_version,
                gold=sample.gold,
                prediction=predicted or {},
                correct=correct,
                status=status,
                reason=reason
                or (
                    "当前没有可独立评测的AI输出，或结果已由人工覆盖。"
                    if status == "unavailable"
                    else ""
                ),
            )
        counts.update(
            accuracy=ratio(counts["correct"], counts["evaluated"]),
            precision=ratio(counts["tp"], counts["tp"] + counts["fp"]),
            recall=ratio(counts["tp"], counts["tp"] + counts["fn"]),
            miss_rate=ratio(counts["fn"], counts["tp"] + counts["fn"]),
            false_positive_rate=ratio(counts["fp"], counts["fp"] + counts["tn"]),
            citation_grounding=ratio(counts["grounded_quotes"], counts["quotes"]),
            human_evidence_support=ratio(
                counts["human_evidence_supported"], counts["human_evidence_checked"]
            ),
        )
        metrics[kind] = counts
    run.metrics = metrics
    run.status = "completed" if any(m["evaluated"] for m in metrics.values()) else "no_labels"
    run.save()
    AuditRecord.objects.create(
        actor=actor, action="quality.evaluated", object_id=run.pk, details={"status": run.status}
    )
    return run
