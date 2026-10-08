"""Business outcomes and redacted operations are separate API contracts."""
from .workflow import report


def workflow_result(run):
    value = report(run)
    gap = value.get("gap_fill") or {}
    return {
        "items": [{key: item[key] for key in ("id", "policy_id", "title", "can_open")} for item in value["items"] if item["can_open"]],
        "gap_fill": {"fields": gap.get("fields", []), "run_id": gap.get("run_id") if gap.get("can_open") else None,
                     "can_open": bool(gap.get("can_open"))},
        "notice": "基于已确认资料和政策原文提供参考，不代表已具备申报资格。",
    }
