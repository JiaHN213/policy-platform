"""Versioned, bounded policy passages; parsed attachments are part of policy.body."""
import hashlib
import json
import re

from policies.catalog import batch_state, visible_batches, visible_opportunities
from policies.readiness import evidence_readiness


def has_active_opportunity(user, policy):
    closed = {"closed", "suspended", "publicity", "completed"}
    batches = {}
    for batch in visible_batches(user).filter(opportunity__policy=policy):
        batches.setdefault(batch.opportunity_id, []).append(batch)
    return any(o.status not in closed and (not batches.get(o.pk) or any(batch_state(b)[0] not in closed for b in batches[o.pk]))
               for o in visible_opportunities(user).filter(policy=policy))


def policy_signature(user, policy):
    opportunities = list(visible_opportunities(user).filter(policy=policy).order_by("id").values())
    batches = list(visible_batches(user).filter(opportunity__policy=policy).order_by("id"))
    payload = ["match-analysis-v2", policy.version, policy.status, policy.validity_status,
               policy.title, policy.source_grade, policy.business_domains, policy.direction_tags,
               policy.city, policy.province, policy.geographic_level,
               policy.body, opportunities,
               [(str(b.pk), b.updated_at, batch_state(b), b.starts_at, b.deadline_at) for b in batches],
               evidence_readiness(policy)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def passage(policy, start, end):
    marker = list(re.finditer(r"(?m)^附件[：:].*", policy.body[:start + 1]))
    title = marker[-1][0] if marker else "政策正文"
    return {"source_id": f"body:{start}", "text": policy.body[start:end], "start_offset": start,
            "end_offset": end, "source_name": title[:200], "source_type": "attachment" if marker else "body",
            "policy_id": str(policy.pk), "policy_version": policy.version, "url": policy.source_url}


def select_passages(policy, terms, conditions, budget=18000):
    # Score chunks throughout the document, not only its opening. Input is bounded,
    # so always report partial coverage instead of claiming to read a whole file.
    candidates = []
    for start in range(0, len(policy.body), 1500):
        text = policy.body[start:start + 2000]
        score = sum(3 for term in terms if term and term in text)
        score += sum(10 for term in conditions if term and term in text)
        score += min(5, len(re.findall(r"申报条件|申报主体|注册地|项目实施地|总投资|营业收入|不得|须具备|申请条件", text)))
        candidates.append((score, start))
    selected, used = [], 0
    for _, start in sorted(candidates, key=lambda item: (-item[0], item[1])):
        item = passage(policy, start, min(len(policy.body), start + 2000))
        if used + len(item["text"]) > budget:
            continue
        selected.append(item)
        used += len(item["text"])
    selected.sort(key=lambda item: item["start_offset"])
    covered, end = 0, 0
    for item in selected:
        covered += max(0, item["end_offset"] - max(end, item["start_offset"]))
        end = max(end, item["end_offset"])
    return selected, {"selected_characters": used, "covered_characters": covered,
                      "total_characters": len(policy.body), "partial": covered < len(policy.body)}


def condition_source(policy, quote, start=0):
    offset = policy.body.find(quote, start)
    if offset < 0:
        return None
    item = passage(policy, offset, offset + len(quote))
    # Do not turn a substring of an exception, alternative or encouragement into
    # a hard condition. Numbered complete clauses are allowed.
    left = max(policy.body.rfind(sep, 0, offset) for sep in "。；;\n") + 1
    ends = [pos for sep in "。；;\n" if (pos := policy.body.find(sep, offset + len(quote.rstrip("。；;\n")) )) >= 0]
    right = min(ends) if ends else len(policy.body)
    clause = policy.body[left:right].strip().rstrip("。；;")
    clause = re.sub(r"^(?:[（(]?[一二三四五六七八九十\d]+[）)、.．]\s*)", "", clause)
    item["complete_clause"] = clause == quote.strip().rstrip("。；;")
    return item
