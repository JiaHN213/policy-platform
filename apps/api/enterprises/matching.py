"""Explainable relevance, never a certification of application eligibility."""
from django.utils import timezone
from policies.catalog import batch_state, formal_policies, visible_batches, visible_opportunities
from policies.readiness import evidence_readiness

from .conditions import assess_conditions
from .fields import tag_options
from .regions import region_preference
from .retrieval import filter_policy, index_candidates, recall_terms

LEVELS = {"high": "高相关", "medium": "中相关", "low": "低相关", "insufficient": "信息不足"}
INACTIVE = {"expired", "repealed", "replaced", "consultation"}
CLOSED = {"closed", "suspended", "publicity", "completed"}


def match_policies(user, profile, project=None, view="policies", filters=None, *, policy_ids=None):
    filters = filters or {}
    options = tag_options()
    data = profile.data
    # Project matching uses project tags, so a company's other lines of business
    # cannot inflate the relevance of an unrelated project.
    target = project.data if project else data
    domains = set(target.get("business_domains", []))
    directions = set(target.get("direction_tags", []))
    terms = recall_terms(target)
    index_ids, backend = index_candidates(user, terms) if policy_ids is None else (set(), "database")
    labels = {o["value"]: o["label"] for items in options.values() for o in items}
    query = formal_policies(user).exclude(validity_status__in=INACTIVE).prefetch_related("snapshots", "discovereditem_set")
    if policy_ids is not None:
        query = query.filter(pk__in=policy_ids)
    opportunity_map, batch_map = {}, {}
    opportunities_query = visible_opportunities(user).select_related("policy")
    batches_query = visible_batches(user)
    if policy_ids is not None:
        opportunities_query = opportunities_query.filter(policy_id__in=policy_ids)
        batches_query = batches_query.filter(opportunity__policy_id__in=policy_ids)
    for opportunity in opportunities_query:
        if filters.get("category") and opportunity.category != filters["category"]:
            continue
        if filters.get("opportunity_status") and opportunity.status != filters["opportunity_status"]:
            continue
        opportunity_map.setdefault(opportunity.policy_id, []).append(opportunity)
    for batch in batches_query:
        batch_map.setdefault(batch.opportunity_id, []).append(batch)
    if view == "opportunities":
        query = query.filter(pk__in=opportunity_map)
    items, excluded, conflicts = [], 0, 0
    now = timezone.now()
    for policy in query.iterator(chunk_size=200):
        if not filter_policy(policy, filters):
            continue
        opportunities = opportunity_map.get(policy.pk, [])
        active, deadlines = [], []
        for opportunity in opportunities:
            batches = batch_map.get(opportunity.pk, [])
            usable = [b for b in batches if batch_state(b, now)[0] not in CLOSED]
            if opportunity.status in CLOSED or (batches and not usable):
                continue
            active.append(opportunity)
            deadlines += [b.deadline_at for b in usable if b.deadline_at]
        if view == "opportunities" and not active:
            excluded += 1
            continue
        matched_domains = sorted(domains & set(policy.business_domains))
        matched_directions = sorted(directions & set(policy.direction_tags))
        text_terms = [term for term in terms if term in policy.title or term in policy.body]
        gaps, reasons = [], []
        if matched_domains:
            reasons.append("业务领域相符：" + "、".join(labels.get(v, v) for v in matched_domains))
        if matched_directions:
            reasons.append("技术方向相符：" + "、".join(labels.get(v, v) for v in matched_directions))
        if text_terms:
            reasons.append("正文或标题涉及已确认方向：" + "、".join(text_terms[:5]) + "（提及不等于适用）")
        if not domains and not directions:
            gaps.append("请确认" + ("项目" if project else "企业") + "的业务领域或技术方向。")
        if not policy.business_domains and not policy.direction_tags:
            gaps.append("该政策尚无可比较的业务标签。")
        if policy.validity_status == "unverified":
            gaps.append("政策效力尚待核实。")
        # A policy issuer's location is not a binding registration requirement.
        # Retain regional candidates and expose uncertainty instead of excluding them.
        if policy.geographic_level != "national":
            target_city = target.get("city") if project else data.get("city")
            gaps.append("需核对项目实施地或企业注册地是否在政策适用范围内。")
            if target_city and policy.city and target_city == policy.city:
                reasons.append("所在城市与发布城市一致，具体适用范围以原文为准。")
        requirements = []
        for opportunity in active:
            requirements += [str(v) for v in opportunity.requirements + opportunity.exclusion_conditions + opportunity.prerequisites]
            if opportunity.status == "unverified":
                gaps.append("政策机会的受理状态尚待核实。")
        if requirements:
            gaps.append("申报条件需逐项核对，相关度不代表已满足申报资格。")
        score = min(100, len(matched_domains) * 30 + len(matched_directions) * 15)
        if (not domains and not directions) or (not policy.business_domains and not policy.direction_tags):
            level = "insufficient"
        elif matched_domains and matched_directions:
            level = "high"
        elif matched_domains or matched_directions:
            level = "medium"
        else:
            level = "low"
        if text_terms and level in {"low", "insufficient"}:
            level = "medium"
            score = max(score, 10)
        elif str(policy.pk) in index_ids and level == "low":
            reasons.append("全文检索发现相关线索，需查看原文判断适用性。")
            score = max(score, 5)
        readiness = evidence_readiness(policy)
        region_match = region_preference(policy, target.get("interest_regions") or ([target["city"]] if project and target.get("city") else data.get("interest_regions", [])), active)
        conditions = assess_conditions(policy, active, data, project.data if project else None, readiness, batch_map)
        if conditions["status"] == "conflict":
            conflicts += 1
            if not filters.get("include_conflicts", False):
                continue
        priority_ready = level in {"high", "medium"} and conditions["status"] == "consistent" and readiness["status"] == "available"
        items.append({"policy_id": str(policy.id), "title": policy.title, "summary": policy.summary,
                      "evidence_readiness": readiness, "conditions": conditions,
                      "supplement_fields": list(dict.fromkeys(c["profile_field"] for g in conditions["opportunities"] for c in g["checks"] if c["status"] == "unknown" and c["profile_field"]))[:2],
                      "retrieval": {"tags": bool(matched_domains or matched_directions), "terms": text_terms[:5], "index_hit": str(policy.pk) in index_ids},
                      "region_preference": region_match,
                      "recommendation_group": "priority" if priority_ready else "needs_verification",
                      "recommendation_label": "优先查看（已核对条件相符）" if priority_ready else "相关线索 · 待核对条件",
                      "policy_version": policy.version, "region": policy.region,
                      "publication_date": policy.publication_date.isoformat(),
                      "level": level, "level_label": LEVELS[level], "score": score,
                      "reasons": reasons or ["暂未发现已确认标签的交集。"],
                      "gaps": list(dict.fromkeys(gaps)), "requirements": requirements[:12],
                      "opportunities": [{"id": str(o.id), "title": o.title, "support_content": o.support_content,
                          "status": o.status, "batches": [{"id": str(b.pk), "status": batch_state(b, now)[0],
                              "starts_at": b.starts_at.isoformat() if b.starts_at else None,
                              "deadline_at": b.deadline_at.isoformat() if b.deadline_at else None} for b in batch_map.get(o.pk, [])]} for o in active],
                      "deadline": min(deadlines).isoformat() if deadlines else None})
    priority = {"high": 3, "medium": 2, "low": 1, "insufficient": 0}
    items.sort(key=lambda x: (x["recommendation_group"] == "priority", x["region_preference"]["matched"], priority[x["level"]], x["score"], x["publication_date"], x["policy_id"]), reverse=True)
    if filters.get("sort") == "latest":
        items.sort(key=lambda x: (x["publication_date"], x["policy_id"]), reverse=True)
    elif filters.get("sort") == "relevance":
        items.sort(key=lambda x: (priority[x["level"]], x["score"], x["publication_date"], x["policy_id"]), reverse=True)
    elif filters.get("sort") == "deadline":
        items.sort(key=lambda x: (x["deadline"] is None, x["deadline"] or "", x["policy_id"]))
    return {"items": items, "count": len(items), "excluded_closed_opportunities": excluded,
            "conflicting_policies": conflicts, "search_backend": backend,
            "counts": {key: sum(item["level"] == key for item in items) for key in LEVELS},
            "profile_revision": profile.revision, "project_revision": project.revision if project else None,
            "notice": "依据已确认画像、标签与全文提供相关度参考，不作申报资格认定。仅明确硬条件冲突默认排除，信息不足继续保留；机会视角排除已截止等不可申报状态。"}
