"""Wiki LLM policy-relation discovery with deterministic evidence validation."""

import hashlib
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from core.ai_runtime import get_ai_profile
from core.business_config import checksum, get_config
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from policies.enrichment import model_json
from policies.models import Policy, PolicyRelation
from policies.taxonomy import RelationKind
from pydantic import BaseModel, ConfigDict, Field

from .models import KnowledgeRelationScan, RelationReviewCandidate

_BASE_RELATION_CONFIG = get_config("wiki_relations", database=False)
PROMPT_VERSION = "wiki-relations-pairs-v2"
VALIDATOR_VERSION = "wiki-relations-evidence-v3"
DIRECTIONS = ";".join(
    f"{key}:{value}" for key, value in _BASE_RELATION_CONFIG["directions"].items()
)
RELATION_CUES = tuple(_BASE_RELATION_CONFIG["relation_cues"])
NEWER_SOURCE_KINDS = set(_BASE_RELATION_CONFIG["newer_source_kinds"])
KIND_CUES = {key: tuple(value) for key, value in _BASE_RELATION_CONFIG["kind_cues"].items()}


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RelationProposal(StrictOutput):
    from_policy_id: str
    to_policy_id: str
    relation: str
    evidence_policy_id: str
    evidence_quote: str = Field(min_length=5, max_length=3000)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=600)


class RelationAuditOutput(StrictOutput):
    relations: list[RelationProposal] = Field(max_length=16)


class PairProposal(StrictOutput):
    direction: Literal["A_to_B", "B_to_A"]
    relation: RelationKind
    evidence_excerpt_id: str
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=600)


class PairAuditOutput(StrictOutput):
    relations: list[PairProposal] = Field(max_length=4)


def aligned_quote(quote, body):
    """Ignore whitespace only, and return an exact span of the saved source."""
    if not quote or len(re.sub(r"\s", "", quote)) < 5:
        return ""
    if quote in body:
        return quote
    positions = [i for i, char in enumerate(body) if not char.isspace()]
    compact = "".join(body[i] for i in positions)
    needle = re.sub(r"\s", "", quote)
    start = compact.find(needle)
    if start < 0 or compact.find(needle, start + 1) >= 0:
        return ""
    return body[positions[start] : positions[start + len(needle) - 1] + 1]


def proposal_diagnostics(proposal, locked, anchor_id, candidate_ids):
    """Explain every structural failure, instead of collapsing unrelated checks."""
    errors = []
    pair = {proposal.from_policy_id, proposal.to_policy_id}
    if len(pair) != 2:
        errors.append("关系起点和终点是同一份文件；请选择两份不同文件。")
    if anchor_id not in pair:
        errors.append("模型关联了两份候选文件，未包含本轮主文件；需要对这两份文件单独核验。")
    if not pair.issubset({anchor_id, *candidate_ids}):
        errors.append("关系包含本轮未提供的文件，无法核对文件身份。")
    if proposal.relation not in RelationKind.values:
        errors.append("模型使用了未支持的关系类型；请重新选择关系类型。")
    evidence = locked.get(proposal.evidence_policy_id)
    canonical = ""
    if not evidence:
        errors.append("未找到模型指定的证据文件或原文片段；请重新选择证据。")
    elif proposal.evidence_policy_id not in pair:
        errors.append(f"证据来自《{evidence.title}》，不属于关系两端文件。")
    else:
        canonical = aligned_quote(proposal.evidence_quote, evidence.body)
        if not canonical:
            if _normalized(proposal.evidence_quote) in _normalized(evidence.body):
                errors.append(
                    "引用与原文存在标点或其他符号差异，不能仅删除符号后认定一致；请重新选取原文。"
                )
            else:
                errors.append(
                    "引用在所选证据文件中不是连续原文，可能被省略、拼接、改写或选错来源；请重新选取原文。"
                )
    from_policy = locked.get(proposal.from_policy_id)
    to_policy = locked.get(proposal.to_policy_id)
    if from_policy and to_policy and evidence and proposal.evidence_policy_id in pair:
        semantic = _semantic_validation(
            from_policy,
            to_policy,
            evidence,
            canonical or proposal.evidence_quote,
            proposal.relation,
        )
        if semantic:
            errors.append(semantic)
    return errors, canonical


def candidate_diagnostics(candidate):
    policies = [candidate.from_policy, candidate.to_policy, candidate.evidence_policy]
    locked = {str(p.pk): p for p in policies if p}
    if any(candidate.source_versions.get(key) != p.version for key, p in locked.items()):
        return "相关政策原文版本已经变化；历史结论不能直接复用，请按当前全文重新核验。"
    proposal = RelationProposal(
        from_policy_id=str(candidate.from_policy_id),
        to_policy_id=str(candidate.to_policy_id),
        relation=candidate.proposed_kind,
        evidence_policy_id=str(candidate.evidence_policy_id or ""),
        evidence_quote=candidate.evidence_quote or "未提供原文证据",
        confidence=float(candidate.confidence or 0),
        reason=candidate.model_reason or "历史候选",
    )
    errors, canonical = proposal_diagnostics(
        proposal,
        locked,
        str(candidate.scan.anchor_policy_id),
        set(candidate.scan.candidate_versions),
    )
    notes = []
    if canonical and canonical != candidate.evidence_quote:
        notes.append("原引用仅有空格或换行差异，现可定位到连续原文。")
    if errors:
        notes.extend(errors)
    else:
        notes.append("按当前规则重检未发现硬性冲突；仍需人工核对关系语义，不自动确认历史候选。")
    return "\n".join(notes)


@dataclass
class CandidateGroup:
    anchor: Policy
    candidates: list[Policy]
    scores: dict[str, int]


def enabled():
    return bool(
        getattr(settings, "KNOWLEDGE_LLM_ENABLED", True)
        and getattr(settings, "KNOWLEDGE_RELATION_AUDIT_ENABLED", True)
        and get_ai_profile("wiki_relations").configured
    )


def _normalized(value):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", (value or "").casefold())


def _terms(policy):
    return {
        _normalized(item.get("term", ""))
        for item in policy.structured_keywords or []
        if len(_normalized(item.get("term", ""))) >= 2
    }


def _features(policy):
    return {
        "title": _normalized(policy.title),
        "body": _normalized(policy.body),
        "number": _normalized(policy.document_number),
        "issuer": _normalized(policy.issuer),
        "terms": _terms(policy),
        "domains": set(policy.business_domains or []),
    }


def _score(anchor, other, features, relation_cues=None):
    anchor_features = features[str(anchor.pk)]
    other_features = features[str(other.pk)]
    anchor_title = anchor_features["title"]
    other_title = other_features["title"]
    anchor_body = anchor_features["body"]
    other_body = other_features["body"]
    score = 0
    strong = False
    if len(other_title) >= 6 and other_title in anchor_body:
        score += 120
        strong = True
    if len(anchor_title) >= 6 and anchor_title in other_body:
        score += 100
        strong = True
    anchor_number = anchor_features["number"]
    other_number = other_features["number"]
    if other_number and other_number in anchor_body:
        score += 140
        strong = True
    if anchor_number and anchor_number in other_body:
        score += 120
        strong = True
    similarity = SequenceMatcher(None, anchor_title, other_title).ratio()
    if similarity >= 0.88:
        score += 70
        strong = True
    elif similarity >= 0.68:
        score += 25
    common_terms = anchor_features["terms"] & other_features["terms"]
    score += min(len(common_terms) * 6, 30)
    if anchor_features["domains"] & other_features["domains"]:
        score += 10
    if anchor.issuer and anchor_features["issuer"] == other_features["issuer"]:
        score += 6
    if relation_cues is None:
        relation_cues = get_config("wiki_relations").get("relation_cues", RELATION_CUES)
    cue = any(term in anchor.title or term in other.title for term in relation_cues)
    if cue:
        score += 10
    if abs((anchor.publication_date - other.publication_date).days) <= 1096:
        score += 3
    eligible = strong or (cue and score >= 45)
    return score, eligible


def candidate_groups(policies=None, limit=6):
    policies = list(
        policies
        or Policy.objects.filter(
            status="published", source_grade__in=Policy.FORMAL_SOURCE_GRADES, is_demo=False
        ).order_by("publication_date", "id")
    )
    features = {str(policy.pk): _features(policy) for policy in policies}
    relation_cues = get_config("wiki_relations").get("relation_cues", RELATION_CUES)
    groups = []
    for index, anchor in enumerate(policies):
        ranked = []
        for other in policies[:index]:
            score, eligible = _score(anchor, other, features, relation_cues)
            if eligible:
                ranked.append((score, other.publication_date, str(other.pk), other))
        ranked.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
        selected = [item[3] for item in ranked[:limit]]
        groups.append(
            CandidateGroup(
                anchor=anchor,
                candidates=selected,
                scores={str(item[3].pk): item[0] for item in ranked[:limit]},
            )
        )
    return groups


def _excerpt(policy, related, limit):
    body = policy.body
    if len(body) <= limit:
        return body
    windows = [(0, min(4500, len(body))), (max(0, len(body) - 1800), len(body))]
    clues = [*re.findall(r"《([^》]{3,100})》", body)[:15]]
    for item in related:
        clues.extend([item.title, item.document_number])
    for clue in clues:
        if not clue:
            continue
        position = body.find(clue)
        if position >= 0:
            windows.append((max(0, position - 900), min(len(body), position + len(clue) + 1400)))
    pieces = []
    used = 0
    for start, end in sorted(windows):
        piece = body[start:end]
        remaining = limit - used
        if remaining <= 0:
            break
        pieces.append(piece[:remaining])
        used += len(pieces[-1])
    return "\n【同一原文的另一处摘录】\n".join(pieces)


def _scan_payload(group):
    documents = []
    for policy in [group.anchor, *group.candidates]:
        counterparts = group.candidates if policy == group.anchor else [group.anchor]
        documents.append(
            {
                "id": str(policy.pk),
                "version": policy.version,
                "title": policy.title,
                "document_number": policy.document_number,
                "issuer": policy.issuer,
                "publication_date": policy.publication_date.isoformat(),
                "document_type": policy.document_type,
                "summary": policy.summary[:1200],
                "body_excerpt": _excerpt(
                    policy, counterparts, 11000 if policy == group.anchor else 6000
                ),
            }
        )
    return {
        "anchor_policy_id": str(group.anchor.pk),
        "candidate_scores": group.scores,
        "documents": documents,
    }


def pair_payload(group):
    """Bounded, exact, overlapping source spans; no generated summary as evidence."""
    documents, evidence_map = [], {}
    for role, policy, other in (
        ("A", group.anchor, group.candidates[0]),
        ("B", group.candidates[0], group.anchor),
    ):
        windows = []
        for start in range(0, len(policy.body), 800):
            piece = policy.body[start : start + 1000]
            score = 100 if _references_policy(piece, other) else 0
            if start == 0 or start + 1000 >= len(policy.body):
                score += 10
            windows.append((score, start, piece))
        selected = sorted(
            sorted(windows, key=lambda row: (-row[0], row[1]))[:6], key=lambda row: row[1]
        )
        excerpts = []
        for _, start, piece in selected:
            key = f"{role}-{start}"
            excerpts.append({"id": key, "text": piece})
            evidence_map[key] = (policy, piece)
        documents.append(
            {
                "role": role,
                "title": policy.title,
                "document_number": policy.document_number,
                "publication_date": policy.publication_date.isoformat(),
                "document_type": policy.document_type,
                "excerpts": excerpts,
            }
        )
    return {"anchor_policy_id": str(group.anchor.pk), "documents": documents}, evidence_map


def resolve_pair_output(group, output, evidence_map):
    proposals = []
    for proposal in output.relations:
        left, right = (group.anchor, group.candidates[0])
        if proposal.direction == "B_to_A":
            left, right = right, left
        evidence, quote = evidence_map.get(proposal.evidence_excerpt_id, (None, "未找到引用片段"))
        proposals.append(
            RelationProposal(
                from_policy_id=str(left.pk),
                to_policy_id=str(right.pk),
                relation=proposal.relation.value,
                evidence_policy_id=str(evidence.pk) if evidence else "",
                evidence_quote=quote,
                confidence=proposal.confidence,
                reason=proposal.reason,
            )
        )
    return RelationAuditOutput(relations=proposals)


def _references_policy(quote, policy):
    normalized_quote = _normalized(quote)
    title = _normalized(policy.title)
    number = _normalized(policy.document_number)
    if number and number in normalized_quote:
        return True
    if len(title) >= 6 and title in normalized_quote:
        return True
    for reference in re.findall(r"《([^》]{3,160})》", quote):
        normalized_reference = _normalized(reference)
        if len(normalized_reference) < 5:
            continue
        if (
            normalized_reference in title
            or title in normalized_reference
            or SequenceMatcher(None, normalized_reference, title).ratio() >= 0.72
        ):
            return True
    return False


def _semantic_validation(from_policy, to_policy, evidence_policy, quote, kind):
    counterpart = to_policy if evidence_policy.pk == from_policy.pk else from_policy
    similar_titles = (
        SequenceMatcher(None, _normalized(from_policy.title), _normalized(to_policy.title)).ratio()
        >= 0.88
    )
    if not _references_policy(quote, counterpart) and not (kind == "finalizes" and similar_titles):
        return f"所选证据未明确指向《{counterpart.title}》的名称或文号；主题相似或共同引用上位文件不能证明两者直接相关。"
    newer_source_kinds = set(
        get_config("wiki_relations").get("newer_source_kinds", NEWER_SOURCE_KINDS)
    )
    if kind in newer_source_kinds and from_policy.publication_date < to_policy.publication_date:
        return f"时间顺序不符：起点《{from_policy.title}》（{from_policy.publication_date}）早于终点《{to_policy.title}》（{to_policy.publication_date}）；实施、修订、废止等关系通常应由新文件指向旧文件，请核对方向。"
    if (
        kind in {"superior", "finalizes"}
        and from_policy.publication_date > to_policy.publication_date
    ):
        return f"时间顺序不符：起点《{from_policy.title}》（{from_policy.publication_date}）晚于终点《{to_policy.title}》（{to_policy.publication_date}）；请核对上位→下位或征求意见稿→正式文件的方向及发布日期。"
    material = from_policy.title + "\n" + quote
    configured_cues = get_config("wiki_relations").get("kind_cues", {})
    required = configured_cues.get(kind, KIND_CUES.get(kind))
    if required and not any(term in material for term in required):
        return f"“{RelationKind(kind).label}”关系缺少动作词依据；当前规则要求出现：{'、'.join(required)}。请核对是否选错关系类型。"
    if kind == "revises":
        targeted = get_config("wiki_relations").get("targeted_revision_cues", ["调整", "变更"])
        ordinary = [term for term in (required or []) if term not in targeted]
        if not any(term in quote for term in ordinary):
            # Adjustment must act on the referenced document, not on some unrelated project.
            references = list(re.finditer(r"《[^》]+》(?:（[^）]{0,80}）)?", quote))
            targets = [
                match for match in references if _references_policy(match.group(), to_policy)
            ]
            if evidence_policy.pk != from_policy.pk or not any(
                re.search(r"(?:对|将)\s*$", quote[max(0, m.start() - 8) : m.start()])
                and any(
                    term in re.split(r"[。；;]", quote[m.end() : m.end() + 80])[0]
                    for term in targeted
                )
                for m in targets
            ):
                return "发现调整或变更表述，但未确认其直接作用于被引用的旧文件；仅调整项目、工作安排或引用政策依据不能自动认定为修订。"
    if kind == "finalizes" and not (
        from_policy.document_type == "draft" or "征求意见" in from_policy.title
    ):
        return "征求意见转正式关系的起点不是征求意见稿"
    return ""


def relation_runtime_signature():
    return checksum(
        {
            "prompt": PROMPT_VERSION,
            "validator": VALIDATOR_VERSION,
            "rules": checksum(get_config("wiki_relations")),
            "model": get_ai_profile("wiki_relations").model,
        }
    )


def _input_hash(group, runtime=None):
    value = {
        "prompt": PROMPT_VERSION,
        "runtime": runtime or relation_runtime_signature(),
        "anchor": [str(group.anchor.pk), group.anchor.version, group.anchor.content_hash],
        "candidates": [
            [str(policy.pk), policy.version, policy.content_hash] for policy in group.candidates
        ],
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _persist(group, digest, output):
    ids = {group.anchor.pk, *(policy.pk for policy in group.candidates)}
    with transaction.atomic():
        locked = {
            str(policy.pk): policy
            for policy in Policy.objects.select_for_update().filter(pk__in=ids).order_by("id")
        }
        expected = {str(policy.pk): policy.version for policy in [group.anchor, *group.candidates]}
        if set(locked) != set(expected) or any(
            locked[key].version != version
            or locked[key].status != "published"
            or locked[key].source_grade not in Policy.FORMAL_SOURCE_GRADES
            for key, version in expected.items()
        ):
            raise ValueError("WIKI_RELATION_SOURCE_CHANGED")

        accepted, invalid, preserved = [], [], 0
        rejected_for_review = []
        proposed_keys = set()
        anchor_id = str(group.anchor.pk)
        candidate_ids = {str(policy.pk) for policy in group.candidates}
        for proposal in output.relations:
            from_policy = locked.get(proposal.from_policy_id)
            to_policy = locked.get(proposal.to_policy_id)
            evidence = locked.get(proposal.evidence_policy_id)
            errors, canonical_quote = proposal_diagnostics(
                proposal, locked, anchor_id, candidate_ids
            )
            if errors:
                rejection_reason = "\n".join(errors)
                invalid.append({"reason": rejection_reason, "proposal": proposal.model_dump()})
                if from_policy and to_policy and from_policy.pk != to_policy.pk:
                    rejected_for_review.append((proposal, rejection_reason, evidence))
                continue
            key = (proposal.from_policy_id, proposal.to_policy_id, proposal.relation)
            proposed_keys.add(key)
            defaults = {
                "evidence_policy": evidence,
                "evidence_version": evidence.version,
                "evidence_quote": canonical_quote,
                "discovery": {
                    "method": "wiki_llm",
                    "scan_anchor_id": anchor_id,
                    "input_hash": digest,
                    "model": get_ai_profile("wiki_relations").model,
                    "prompt_version": PROMPT_VERSION,
                    "validator_version": VALIDATOR_VERSION,
                    "quote_alignment": "whitespace"
                    if canonical_quote != proposal.evidence_quote
                    else "exact",
                    "confidence": proposal.confidence,
                    "reason": proposal.reason,
                    "versions": expected,
                },
                "verification_status": "verified",
                "verified_at": timezone.now(),
            }
            relation = PolicyRelation.objects.filter(
                from_policy_id=proposal.from_policy_id,
                to_policy_id=proposal.to_policy_id,
                kind=proposal.relation,
            ).first()
            if relation and (
                relation.discovery.get("method") != "wiki_llm"
                or relation.verified_by_id
                or relation.verification_status == "rejected"
            ):
                preserved += 1
                continue
            if relation:
                for field, value in defaults.items():
                    setattr(relation, field, value)
                relation.verified_by = None
                relation.save()
            else:
                relation = PolicyRelation.objects.create(
                    from_policy_id=proposal.from_policy_id,
                    to_policy_id=proposal.to_policy_id,
                    kind=proposal.relation,
                    **defaults,
                )
            accepted.append(str(relation.pk))
            RelationReviewCandidate.objects.filter(
                from_policy=from_policy,
                to_policy=to_policy,
                proposed_kind=proposal.relation,
                status=RelationReviewCandidate.Status.PENDING,
            ).update(
                status=RelationReviewCandidate.Status.SUPERSEDED,
                relation=relation,
                reviewed_at=timezone.now(),
            )

        stale = []
        for relation in PolicyRelation.objects.filter(from_policy_id__in=ids, to_policy_id__in=ids):
            discovery = relation.discovery or {}
            key = (str(relation.from_policy_id), str(relation.to_policy_id), relation.kind)
            if (
                discovery.get("method") == "wiki_llm"
                and discovery.get("scan_anchor_id") == anchor_id
                and key not in proposed_keys
                and not relation.verified_by_id
            ):
                stale.append(relation.pk)
        if stale:
            PolicyRelation.objects.filter(pk__in=stale).delete()

        result = {
            "candidate_count": len(group.candidates),
            "accepted_relation_ids": accepted,
            "invalid_proposals": invalid,
            "preserved_human_relations": preserved,
            "removed_stale_relations": len(stale),
        }
        scan, _ = KnowledgeRelationScan.objects.update_or_create(
            input_hash=digest,
            defaults={
                "anchor_policy": group.anchor,
                "anchor_version": group.anchor.version,
                "candidate_versions": {
                    str(policy.pk): policy.version for policy in group.candidates
                },
                "status": "succeeded",
                "model": get_ai_profile("wiki_relations").model if group.candidates else "",
                "prompt_version": PROMPT_VERSION,
                "result": result,
                "error_code": "",
            },
        )
        for proposal, rejection_reason, evidence in rejected_for_review:
            proposal_hash = hashlib.sha256(
                json.dumps(
                    {
                        "scan": digest,
                        "from": proposal.from_policy_id,
                        "to": proposal.to_policy_id,
                        "kind": proposal.relation,
                        "evidence_policy": proposal.evidence_policy_id,
                        "quote": proposal.evidence_quote,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            RelationReviewCandidate.objects.get_or_create(
                proposal_hash=proposal_hash,
                defaults={
                    "scan": scan,
                    "from_policy_id": proposal.from_policy_id,
                    "to_policy_id": proposal.to_policy_id,
                    "proposed_kind": proposal.relation,
                    "evidence_policy": evidence,
                    "evidence_quote": proposal.evidence_quote,
                    "confidence": proposal.confidence,
                    "model_reason": proposal.reason,
                    "rejection_reason": rejection_reason,
                    "source_versions": expected,
                },
            )
        return result


def _record_failure(group, digest, code):
    scan, created = KnowledgeRelationScan.objects.get_or_create(
        input_hash=digest,
        defaults={
            "anchor_policy": group.anchor,
            "anchor_version": group.anchor.version,
            "candidate_versions": {str(policy.pk): policy.version for policy in group.candidates},
            "status": "failed",
            "model": get_ai_profile("wiki_relations").model,
            "prompt_version": PROMPT_VERSION,
            "error_code": code,
        },
    )
    if not created:
        scan.status = "failed"
        scan.attempts += 1
        scan.error_code = code
        scan.model = get_ai_profile("wiki_relations").model
        scan.save()


def retire_legacy_ai_relations():
    removed = preserved = 0
    for relation in PolicyRelation.objects.all():
        discovery = relation.discovery or {}
        if discovery.get("method") != "ai":
            continue
        if relation.verification_status == "rejected" or relation.verified_by_id:
            relation.discovery = {
                **discovery,
                "method": "human_override",
                "legacy_method": "ai",
                "locked": True,
            }
            relation.save(update_fields=["discovery", "updated_at"])
            preserved += 1
        else:
            relation.delete()
            removed += 1
    return {"removed": removed, "preserved_human": preserved}


def revalidate_existing_wiki_relations():
    removed = kept = 0
    relations = PolicyRelation.objects.select_related("from_policy", "to_policy", "evidence_policy")
    for relation in relations:
        discovery = relation.discovery or {}
        if discovery.get("method") != "wiki_llm" or relation.verified_by_id:
            continue
        invalid = (
            relation.evidence_version != relation.evidence_policy.version
            or relation.evidence_quote not in relation.evidence_policy.body
            or _semantic_validation(
                relation.from_policy,
                relation.to_policy,
                relation.evidence_policy,
                relation.evidence_quote,
                relation.kind,
            )
        )
        if invalid:
            relation.delete()
            removed += 1
        else:
            if discovery.get("validator_version") != VALIDATOR_VERSION:
                relation.discovery = {
                    **discovery,
                    "validator_version": VALIDATOR_VERSION,
                }
                relation.save(update_fields=["discovery", "updated_at"])
            kept += 1
    return {"removed": removed, "kept": kept}


def apply_derived_validity():
    policies = {
        str(policy.pk): policy
        for policy in Policy.objects.filter(status="published", is_demo=False)
    }
    effects = {}
    relations = PolicyRelation.objects.filter(
        verification_status="verified", kind__in=["repeals", "replaces", "revises", "finalizes"]
    ).select_related("evidence_policy")
    for relation in relations:
        if relation.evidence_version != relation.evidence_policy.version:
            continue
        target_id = (
            str(relation.from_policy_id)
            if relation.kind == "finalizes"
            else str(relation.to_policy_id)
        )
        status = "repealed" if relation.kind == "repeals" else "replaced"
        priority = 2 if status == "repealed" else 1
        if target_id in policies and priority > effects.get(target_id, {}).get("priority", 0):
            effects[target_id] = {"status": status, "relation": relation, "priority": priority}

    changed = 0
    for policy_id, policy in policies.items():
        evidence = dict(policy.scope_evidence or {})
        marker = evidence.get("wiki_validity")
        correction_fields = set(
            evidence.get("ai_review", {}).get("human_correction", {}).get("fields", [])
        )
        if "validity_status" in correction_fields:
            continue
        effect = effects.get(policy_id)
        if effect:
            relation = effect["relation"]
            if not marker:
                marker = {
                    "previous_status": policy.validity_status,
                    "previous_evidence": policy.validity_evidence,
                }
            marker.update(
                {
                    "relation_id": str(relation.pk),
                    "kind": relation.kind,
                    "source_version": relation.evidence_version,
                }
            )
            if (
                policy.validity_status != effect["status"]
                or policy.validity_evidence != relation.evidence_quote
                or evidence.get("wiki_validity") != marker
            ):
                evidence["wiki_validity"] = marker
                Policy.objects.filter(pk=policy.pk).update(
                    validity_status=effect["status"],
                    validity_evidence=relation.evidence_quote,
                    scope_evidence=evidence,
                    updated_at=timezone.now(),
                )
                changed += 1
        elif marker:
            evidence.pop("wiki_validity", None)
            Policy.objects.filter(pk=policy.pk).update(
                validity_status=marker.get("previous_status", "unverified"),
                validity_evidence=marker.get("previous_evidence", ""),
                scope_evidence=evidence,
                updated_at=timezone.now(),
            )
            changed += 1
    return changed


def analyze_pair(group):
    configured = get_config("wiki_relations").get("directions", {})
    directions = ";".join(f"{key}:{value}" for key, value in configured.items()) or DIRECTIONS
    payload, evidence_map = pair_payload(group)
    output = model_json(
        "你负责维护政策知识网络。每次只判断给定A和B两份文件。可返回空数组，"
        "相同主题、不同年度或不同地区同类文件不能证明替代、上位或配套关系。"
        "仅依据原文中的明确文件指向和动作判断，不确定就返回空数组。"
        "direction说明：A_to_B表示A为起点B为终点；B_to_A相反。以下规则中A/B泛指起点/终点："
        + directions
        + "。证据必须选择excerpts中实际存在的片段id作为evidence_excerpt_id，不要编写或缩写原文。"
        "例如新文件B写明旧文件A同时废止，应返回B_to_A和repeals；"
        "地方方案依据国家方案制定通常是实施关系，不是国家批复地方或延期关系。"
        "注意区分直接修订被引用文件与只依据它调整其他项目。不得根据训练记忆补充事实。",
        payload, PairAuditOutput, max_tokens=1600, purpose="wiki_relations",
    )
    return resolve_pair_output(group, output, evidence_map)


def audit_relations(max_model_scans=None):
    legacy = retire_legacy_ai_relations()
    revalidated = revalidate_existing_wiki_relations()
    if not enabled():
        return {
            "enabled": False,
            "legacy": legacy,
            "revalidated": revalidated,
            "scanned": 0,
            "skipped": 0,
        }

    max_model_scans = max_model_scans or getattr(settings, "KNOWLEDGE_RELATION_SCANS_PER_BUILD", 25)
    scanned = skipped = accepted = invalid = model_scans = 0
    pending_groups = []
    groups = [
        CandidateGroup(
            group.anchor, [candidate], {str(candidate.pk): group.scores[str(candidate.pk)]}
        )
        for group in candidate_groups()
        for candidate in (group.candidates or [None])
        if candidate is not None
    ]
    for group in groups:
        digest = _input_hash(group)
        if KnowledgeRelationScan.objects.filter(input_hash=digest, status="succeeded").exists():
            skipped += 1
            continue
        if group.candidates and model_scans >= max_model_scans:
            pending_groups.append(group)
            continue
        try:
            model_scans += 1
            output = analyze_pair(group)
            result = _persist(group, digest, output)
        except Exception:
            _record_failure(group, digest, "WIKI_RELATION_ANALYSIS_FAILED")
            raise
        scanned += 1
        accepted += len(result["accepted_relation_ids"])
        invalid += len(result["invalid_proposals"])
    validity_updates = apply_derived_validity()
    return {
        "enabled": True,
        "legacy": legacy,
        "revalidated": revalidated,
        "scanned": scanned,
        "model_scans": model_scans,
        "skipped": skipped,
        "pending": len(pending_groups),
        "accepted": accepted,
        "invalid_proposals": invalid,
        "validity_updates": validity_updates,
    }
