"""Live evidence completeness, separate from publication and relevance."""


def evidence_readiness(policy):
    snapshots = list(policy.snapshots.all())
    parsed = {s.url for s in snapshots if s.parse_status == "parsed"}
    missing = {s.url for s in snapshots if s.parse_status != "parsed"} - parsed
    unknown = False
    for item in policy.discovereditem_set.all():
        for issue in (item.metadata or {}).get("attachment_issues") or []:
            url = issue.get("url")
            if url and url not in parsed:
                missing.add(url)
            elif not url:
                unknown = True
    incomplete = bool(missing or unknown)
    return {
        "status": "incomplete" if incomplete else "available",
        "label": "关键条件待核对" if incomplete else "已知资料无解析缺口",
        "unresolved_count": len(missing),
        "unresolved_urls": sorted(missing)[:20],
        "unlocated_issue": unknown,
        "reason": (
            "存在未解析资料，可能包含申报条件、名单或附表；可阅读现有正文，不能据此判断已满足全部条件。"
            if incomplete else "当前已登记资料未发现解析缺口；不代表附件齐全或已满足申报条件。"
        ),
    }
