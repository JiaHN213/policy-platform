"""Read-only progress from the same candidate hashes used by the relation worker."""

from collections import Counter
from threading import Lock

from core.business_config import checksum
from django.core.cache import cache
from django.db.models import BooleanField, Case, Count, Value, When
from django.utils import timezone

from .models import KnowledgeBuild, KnowledgeRelationScan, RelationReviewCandidate
from .relations import CandidateGroup, _input_hash, candidate_groups, relation_runtime_signature
from .services import current_relations, formal_policies

_manifest_lock = Lock()


def relation_manifest():
    runtime = relation_runtime_signature()
    # Policy edits, publication changes and classifier outputs invalidate the plan.
    signature = list(formal_policies().order_by("id").values(
        "id", "version", "content_hash", "title", "document_number", "issuer",
        "publication_date", "business_domains", "structured_keywords",
    ))
    for item in signature:
        item["id"] = str(item["id"])
        item["publication_date"] = item["publication_date"].isoformat()
    key = "relation-progress-plan:" + checksum({"policies": signature, "runtime": runtime})
    manifest = cache.get(key)
    if manifest is None:
        with _manifest_lock:
            manifest = cache.get(key)
            if manifest is None:
                digests = []
                for group in candidate_groups():
                    for candidate in group.candidates:
                        pair = CandidateGroup(group.anchor, [candidate], {str(candidate.pk): group.scores[str(candidate.pk)]})
                        digests.append(_input_hash(pair, runtime=runtime))
                manifest = {"hashes": list(dict.fromkeys(digests)), "policy_count": len(signature)}
                cache.set(key, manifest, timeout=3600)
    return manifest


def scoped_candidates(queryset, scope):
    if scope == "all":
        return queryset
    hashes = relation_manifest()["hashes"]
    if scope == "current":
        return queryset.filter(scan__input_hash__in=hashes)
    return queryset.exclude(scan__input_hash__in=hashes)


def relation_progress():
    manifest = relation_manifest()
    scans = list(KnowledgeRelationScan.objects.filter(input_hash__in=manifest["hashes"]).values(
        "status", "result", "updated_at",
    ))
    completed = sum(item["status"] == "succeeded" for item in scans)
    failed = sum(item["status"] == "failed" for item in scans)
    total = len(manifest["hashes"])
    no_relation = sum(
        item["status"] == "succeeded" and not item["result"].get("accepted_relation_ids")
        and not item["result"].get("invalid_proposals") for item in scans
    )
    counts, all_counts = Counter(), Counter()
    rows = RelationReviewCandidate.objects.annotate(in_current=Case(
        When(scan__input_hash__in=manifest["hashes"], then=Value(True)),
        default=Value(False), output_field=BooleanField(),
    )).values("status", "in_current").annotate(count=Count("pk")).order_by()
    for row in rows:
        all_counts[row["status"]] += row["count"]
        if row["in_current"]:
            counts[row["status"]] += row["count"]
    build = KnowledgeBuild.objects.order_by("-created_at", "-id").first()
    active = build and build.status in {"running", "queued"}
    expired = build and build.status == "running" and build.lease_until and build.lease_until <= timezone.now()
    if not total:
        state, label = "empty", "暂无候选政策组合"
    elif expired:
        state, label = "waiting", "任务中断，等待恢复"
    elif completed == total:
        state, label = ("finalizing", "关系分析完成，知识页整理中") if active else ("completed", "关系分析完成")
    elif build and build.status == "failed":
        state, label = "failed", "构建中断，等待重试"
    elif active:
        state, label = ("running", "正在分析关系") if build.status == "running" else ("queued", "等待继续分析")
    else:
        state, label = "waiting", "等待下一次构建"
    return {
        "status": state, "status_label": label,
        "policy_count": manifest["policy_count"], "total": total, "completed": completed,
        "waiting": max(0, total - completed - failed), "failed": failed,
        "percent": round(100 * completed / total, 1) if total else 0,
        "no_relation_pairs": no_relation,
        "verified_relations": current_relations().count(),
        "review": {"current": {status: counts.get(status, 0) for status in RelationReviewCandidate.Status.values},
                   "historical": {status: all_counts.get(status, 0) - counts.get(status, 0) for status in RelationReviewCandidate.Status.values},
                   "all": {status: all_counts.get(status, 0) for status in RelationReviewCandidate.Status.values}},
        "last_scan_at": max((item["updated_at"] for item in scans), default=None),
        "build_id": str(build.pk) if build else None,
        "as_of": timezone.now(),
    }
