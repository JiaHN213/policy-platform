"""Discover missing facts; return grounded drafts, never apply them to profiles."""
from core.ai_runtime import get_ai_profile
from core.models import AuditRecord

from .agent import snapshot_for
from .fields import COMPANY_FIELDS, FIELDS
from .models import ResearchRun, ResearchSettings

# Financial figures and future project plans cannot be reliably inferred from
# public corporate introductions. Keep these as explicit user questions.
RESEARCHABLE = {"business_domains", "direction_tags", "business_summary", "registered_city", "city", "subject_type", "capabilities"}


def gaps(profile, project, rows):
    found = []
    target = project.data if project else profile.data
    if not target.get("business_domains") and not target.get("direction_tags"):
        found.append(("project." if project else "enterprise.") + "business_domains")
    for row in rows:
        found.extend(row.get("supplement_fields", []))
    return [{"field": field, "label": FIELDS[field.split(".")[-1]],
             "researchable": field.startswith("enterprise.") and field.split(".")[-1] in RESEARCHABLE}
            for field in dict.fromkeys(found)
            if "." in field and field.split(".")[-1] in FIELDS
            and not (project.data if field.startswith("project.") and project else profile.data if field.startswith("enterprise.") else {}).get(field.split(".")[-1])][:2]


def create_child(parent, missing):
    """At most one bounded research child; use the previously chosen source mode."""
    fields = [row["field"].split(".")[-1] for row in missing if row["researchable"]]
    if not fields:
        return None, "这些信息需企业补充，不能从公开资料推断。" if missing else "当前未发现需要补查的画像字段。"
    if not get_ai_profile("enterprise").configured:
        return None, "企业资料提取未配置，现有政策匹配分析照常进行。"
    if not parent.profile.organization.membership_set.filter(user=parent.user, active=True, role="admin").exists():
        return None, "需企业管理员核对资料；现有匹配分析照常进行。"
    saved = snapshot_for(parent.profile, user=parent.user)
    if not saved:
        return None, "资料补查功能尚未对当前企业开放，可手动补充。"
    inputs = {"name": parent.profile.organization.name, "gap_fields": fields}
    material = None
    source_id = parent.inputs.get("source_run_id")
    if not source_id and parent.profile.research_method == "materials":
        # Reuse only this user's explicitly confirmed material, not an arbitrary
        # same-name company draft or a different member's private upload.
        record = AuditRecord.objects.filter(actor=parent.user, object_id=parent.profile_id,
            action__in=["enterprise.created", "enterprise.confirmed"], details__source_run_id__isnull=False).order_by("-created_at").first()
        source_id = record.details.get("source_run_id") if record else None
    source = ResearchRun.objects.filter(pk=source_id, user=parent.user, kind="company", status="completed").first() if source_id else None
    if source and (not source.profile_id or source.profile_id == parent.profile_id) and source.inputs.get("name") == inputs["name"] and source.inputs.get("source_mode") in {"text", "file"}:
        inputs.update({key: source.inputs[key] for key in ("source_mode", "introduction", "file_name") if key in source.inputs})
        material = source.uploaded_material
        if inputs["source_mode"] == "file" and not material:
            return None, "原介绍文件不可用，请重新上传或粘贴简介。"
    elif parent.profile.research_method == "website" and parent.profile.data.get("website"):
        inputs.update(source_mode="website", website=parent.profile.data["website"])
    elif parent.profile.research_method == "search" and ResearchSettings.objects.get(key="default").search_ready:
        inputs.update(source_mode="search")
        inputs.update({key: parent.profile.data.get(key, "") for key in ("city", "credit_code")})
    else:
        return None, "没有可继续读取的已授权资料，请上传／粘贴介绍或补充官网；未自动切换资料来源。"
    # Leave most of the shared budget for policy analyses. No unbounded loop.
    saved.update(max_reads=min(saved["max_reads"], 2), max_calls=min(saved["max_calls"], 2))
    child, _ = parent.children.get_or_create(fingerprint="profile-gaps", defaults={
        "user": parent.user, "profile": parent.profile, "kind": "company", "status": "pending",
        "inputs": inputs, "uploaded_material": material,
        "runtime_snapshot": {**parent.runtime_snapshot, "model": get_ai_profile("enterprise").model}, "agent_snapshot": saved})
    return child, "按原资料方式补查，找到的信息仍需核对后才能用于匹配。"


def restrict_draft(run, result):
    if not run.parent_id or not run.inputs.get("gap_fields"):
        return result
    allowed = set(run.inputs["gap_fields"]) & COMPANY_FIELDS.keys()
    candidates = []
    for candidate in result.get("candidates", []):
        data = {key: value for key, value in candidate["data"].items() if key in allowed and not run.profile.data.get(key) and candidate.get("evidence", {}).get(key)}
        if data:
            candidates.append({**candidate, "data": data, "evidence": {key: candidate["evidence"][key] for key in data}})
    return {**result, "candidates": candidates,
            "notice": "仅展示本次缺失字段的有据建议。请逐项核对并采用；确认前不会修改画像或提高匹配结论。"}
