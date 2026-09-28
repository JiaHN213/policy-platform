"""User-facing policy processing stages derived from existing workflow records."""

from django.utils import timezone

from .models import Policy

STAGES = {
    "WAITING_AI": "等待AI审核",
    "AI_REVIEWING": "AI审核中",
    "PUBLISHED": "已发布",
    "EXCLUDED": "已排除",
    "NEEDS_ACTION": "需要处理",
    "WITHDRAWN": "已撤下",
}


def current_enrichment(policy):
    prefetched = getattr(policy, "_prefetched_objects_cache", {}).get("enrichments")
    jobs = prefetched if prefetched is not None else policy.enrichments.all()
    return next(
        (
            job
            for job in sorted(jobs, key=lambda item: item.updated_at, reverse=True)
            if job.policy_version == policy.version
        ),
        None,
    )


def _attachment_incomplete(policy):
    prefetched = getattr(policy, "_prefetched_objects_cache", {}).get("snapshots")
    snapshots = prefetched if prefetched is not None else policy.snapshots.all()
    return any(item.parse_status != "parsed" for item in snapshots)


def _needs_action_reason(policy, job):
    error_code = (job.error_code or "").upper()
    if job.status == "failed":
        if "ATTACH" in error_code or "PARSE" in error_code:
            return (
                "document_incomplete",
                "DOCUMENT_TEXT_INCOMPLETE",
                "正文或附件没有完整解析，AI审核未能完成。",
                "检查正文和附件解析结果，修复后重新提交AI审核。",
            )
        if "QUOTE" in error_code or "EVIDENCE" in error_code:
            return (
                "evidence_conflict",
                "AI_EVIDENCE_INVALID",
                "AI结论中的引用无法在当前正文中得到确认。",
                "查看全文和引用依据，修正资料后重新审核。",
            )
        if "LEASE" in error_code or "TIMEOUT" in error_code:
            return (
                "model_service",
                "AI_PROCESSING_TIMEOUT",
                "AI处理超时或任务执行中断。",
                "重新加入AI审核队列。",
            )
        return (
            "model_service",
            "AI_PROCESSING_FAILED",
            "AI自动审核未能完成。",
            "重新加入AI审核队列；如果再次失败，再检查模型服务和政策正文。",
        )

    result = job.result or {}
    review = result.get("review") or {}
    finalization = result.get("finalization") or {}
    blocking = set(finalization.get("blocking_reasons") or [])
    if "attachments_incomplete" in blocking or _attachment_incomplete(policy):
        return (
            "document_incomplete",
            "ATTACHMENT_TEXT_UNAVAILABLE",
            "政策正文已审核，但仍有附件没有完整解析。",
            "查看附件内容；补充解析后重新审核，或确认现有正文足以支撑发布。",
        )
    if review.get("decision") == "needs_review" or "ai_needs_review" in blocking:
        return (
            "classification_conflict",
            "AI_REVIEW_NEEDS_CONFIRMATION",
            "AI无法根据当前资料形成可自动发布的明确结论。",
            "查看全文、分类依据和证据，只修改存在问题的字段。",
        )
    return (
        "publication_validation",
        "PUBLICATION_REQUIREMENTS_NOT_MET",
        "AI审核已经完成，但当前记录尚未满足自动发布条件。",
        "查看全文、来源、分类和附件，修正阻止发布的字段。",
    )


def policy_pipeline_state(policy, job=None):
    job = current_enrichment(policy) if job is None else job
    updated_at = (job.updated_at if job else policy.updated_at) or timezone.now()

    if policy.status == Policy.Status.WITHDRAWN:
        values = (
            "WITHDRAWN",
            "policy_withdrawn",
            "POLICY_WITHDRAWN",
            "这份政策已经从客户结果中撤下。",
            "如需恢复，请先核对撤下原因和当前政策版本。",
        )
    elif policy.status == Policy.Status.PUBLISHED:
        values = (
            "PUBLISHED",
            "none",
            "POLICY_PUBLISHED",
            "政策已经通过审核并对客户发布。",
            "无需处理；可以查看搜索、通知和Wiki同步进度。",
        )
    elif policy.source_grade == "L4":
        values = (
            "EXCLUDED",
            "source_scope",
            "POLICY_LEAD_ONLY",
            "该记录已标记为非正式线索，不进入正式政策结果。",
            "默认无需处理；取得官方正式来源后再重新收录。",
        )
    elif job is None or job.status == "queued":
        values = (
            "WAITING_AI",
            "none",
            "WAITING_FOR_AI",
            "政策已经进入政策库，正在等待AI审核。",
            "无需人工操作，系统会按照队列顺序处理。",
        )
    elif job.status == "running":
        values = (
            "AI_REVIEWING",
            "none",
            "AI_REVIEW_IN_PROGRESS",
            "AI正在提取摘要、分类、政策机会和原文证据。",
            "无需重复提交，等待当前任务完成。",
        )
    elif job.status == "succeeded" and (job.result or {}).get("review", {}).get(
        "decision"
    ) == "exclude":
        values = (
            "EXCLUDED",
            "none",
            "AI_EXCLUDED",
            "AI确认该文件不进入当前正式政策结果。",
            "默认无需处理；判断有误时可查看全文并进行局部修正。",
        )
    else:
        category, code, reason, next_action = _needs_action_reason(policy, job)
        values = ("NEEDS_ACTION", category, code, reason, next_action)

    stage, category, code, reason, next_action = values
    return {
        "stage": stage,
        "stage_label": STAGES[stage],
        "reason_category": category,
        "reason_code": code,
        "reason": reason,
        "next_action": next_action,
        "updated_at": updated_at.isoformat(),
    }


def policy_pipeline_timeline(policy):
    state = policy_pipeline_state(policy)
    events = []
    discovered = policy.discovereditem_set.order_by("created_at").first()
    if discovered:
        events.append(
            {
                "key": "discovered",
                "label": "发现政策链接",
                "status": "completed",
                "at": discovered.created_at.isoformat(),
                "detail": "系统已从政策来源登记该链接。",
            }
        )

    snapshots = list(policy.snapshots.order_by("created_at", "id"))
    if snapshots:
        complete = all(item.parse_status == "parsed" for item in snapshots)
        events.append(
            {
                "key": "parsed",
                "label": "正文和附件解析",
                "status": "completed" if complete else "warning",
                "at": max(item.updated_at for item in snapshots).isoformat(),
                "detail": (
                    "正文和附件已经完成解析。"
                    if complete
                    else "至少一份正文或附件尚未完整解析。"
                ),
            }
        )

    job = current_enrichment(policy)
    if job:
        ai_status = {
            "queued": "pending",
            "running": "active",
            "succeeded": "completed",
            "failed": "failed",
        }.get(job.status, "pending")
        events.append(
            {
                "key": "ai_review",
                "label": "AI审核",
                "status": ai_status,
                "at": job.updated_at.isoformat(),
                "detail": {
                    "queued": "等待AI审核。",
                    "running": "AI正在处理。",
                    "succeeded": "AI审核和确定性校验已经完成。",
                    "failed": "AI审核未能完成，可以重新提交。",
                }.get(job.status, "等待处理。"),
            }
        )

    if policy.published_at:
        events.append(
            {
                "key": "published",
                "label": "政策发布",
                "status": "completed" if policy.status == "published" else "warning",
                "at": policy.published_at.isoformat(),
                "detail": "政策已经对客户发布。",
            }
        )

        event = policy.publicationevent_set.filter(kind__startswith="policy.").order_by("-created_at").first()
        if event:
            for job in event.consumptions.order_by("consumer"):
                events.append({
                    "key": "search_index" if job.consumer == "search" else job.consumer,
                    "label": job.get_consumer_display(),
                    "status": {"succeeded": "completed", "running": "active", "failed": "failed", "retry": "warning"}.get(job.status, "pending"),
                    "at": (job.succeeded_at or job.updated_at).isoformat(),
                    "detail": job.last_error or job.result.get("message") or job.get_status_display(),
                })

    return {"policy": str(policy.pk), "current": state, "events": events}
