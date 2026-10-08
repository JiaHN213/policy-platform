"""Shared task visibility and execution boundaries; ResearchRun owns lifecycle state."""
from contextlib import contextmanager

from core.ai_runtime import get_ai_profile
from core.ai_usage import task_usage
from django.db import transaction
from django.utils import timezone
from policies.catalog import formal_policies

from .models import ResearchRun, ResearchStep
from .scoped_settings import research_configuration

VERSION = "enterprise-runtime-v1"
KINDS = {"company": "企业资料整理", "project": "项目资料整理", "explanation": "政策匹配解读", "workflow": "自动匹配与解读"}
STATUSES = {"queued": "等待处理", "running": "正在处理", "waiting": "正在协作处理", "completed": "已完成", "failed": "处理失败", "paused": "已停止"}


def snapshot(profile, config, *, kind="company"):
    from .agent import signature
    from .match_analysis import VERSION as analysis_version
    return {"version": VERSION, "profile_revision": profile.revision if profile else None,
            "signature": signature(config), "model": get_ai_profile("enterprise_match" if kind in {"explanation", "workflow"} else "enterprise").model, "analysis_version": analysis_version}


def input_issue(run, *, configuration=False):
    """Never expose old analysis when its policy, project, or enterprise changed."""
    from .agent import enabled_for, signature
    from .matching import INACTIVE
    from .policy_evidence import has_active_opportunity, policy_signature
    run.user.refresh_from_db()
    if not run.user.is_active:
        return "账号已停用。"
    if run.profile_id:
        run.profile.refresh_from_db()
        members = run.profile.organization.membership_set.filter(user=run.user, active=True)
        if run.kind not in {"explanation", "workflow"}:
            members = members.filter(role="admin")
        if not members.exists():
            return "企业访问或编辑权限已变化。"
    saved = run.runtime_snapshot or run.agent_snapshot
    if saved and saved.get("profile_revision") != (run.profile.revision if run.profile_id else None):
        return "企业画像已更新，请基于最新资料创建任务。"
    if run.kind == "workflow":
        from .workflow import input_issue as workflow_issue
        issue = workflow_issue(run, configuration=configuration)
        if issue:
            return issue
    if run.kind == "explanation":
        from .match_analysis import VERSION as analysis_version
        if saved.get("analysis_version", analysis_version) != analysis_version:
            return "政策分析规则已更新，请重新生成解读。"
        policy = formal_policies(run.user).filter(pk=run.inputs.get("policy_id")).first()
        if not policy or policy.version != run.inputs.get("policy_version"):
            return "政策已更新或撤下，请重新选择政策。"
        if not run.profile_id or run.profile.revision != run.inputs.get("profile_revision"):
            return "企业画像已更新，请重新生成解读。"
        project = run.profile.projects.filter(pk=run.inputs.get("project_id")).first() if run.inputs.get("project_id") else None
        if run.inputs.get("project_id") and (not project or project.revision != run.inputs.get("project_revision")):
            return "项目已修改或删除，请重新选择项目。"
        if run.inputs.get("matching_signature") and policy_signature(run.user, policy) != run.inputs["matching_signature"]:
            return "政策条件、批次或资料已变化，请重新生成解读。"
        if policy.validity_status in INACTIVE or (run.inputs.get("matching_view") == "opportunities" and not has_active_opportunity(run.user, policy)):
            return "政策或申报窗口已变化，请刷新匹配结果。"
    if configuration and saved:
        config = research_configuration(run.user, run.profile)
        if not config or signature(config) != saved.get("signature") or (run.runtime_snapshot and saved.get("version") != VERSION):
            return "模型、规则或搜索配置已变化，请重新创建任务。"
        if run.agent_snapshot and not enabled_for(config, run.profile):
            return "当前企业的资料补查功能已关闭。"
        if not (config.matching_allowed if run.kind in {"explanation", "workflow"} else config.research_allowed):
            return "管理员已关闭当前企业的这项整理或匹配服务。"
    return ""


class Execution:
    def __init__(self, run, token):
        self.run, self.token, self.step = run, token, None
        self.completed = False

    def guard(self):
        from .agent import Halted
        current = ResearchRun.objects.filter(pk=self.run.pk, status="running", lease_token=self.token).exists()
        if not current:
            raise Halted("任务已停止，本次返回结果不会应用。")
        issue = input_issue(self.run, configuration=True)
        if issue:
            raise Halted(issue)
        if self.run.parent_id:
            from .workflow import parent_guard
            parent_guard(self.run)

    def before_request(self):
        self.guard()
        if self.run.parent_id:
            from .workflow import reserve_request
            reserve_request(self.run, self.token)

    def progress(self, label):
        self.guard()
        with transaction.atomic():
            run = ResearchRun.objects.select_for_update().get(pk=self.run.pk)
            if run.status != "running" or run.lease_token != self.token:
                self.guard()
            if self.step:
                ResearchStep.objects.filter(pk=self.step.pk, status="running").update(status="completed", finished_at=timezone.now())
            self.step = ResearchStep.objects.create(run=run, sequence=run.steps.count() + 1,
                label=label, tool="workflow", execution_token=self.token)
            run.stage = label
            run.save(update_fields=["stage", "updated_at"])


@contextmanager
def tracking(run, token):
    execution = Execution(run, token)
    with task_usage(run.pk, step_id=lambda: execution.step.pk if execution.step else None, guard=execution.before_request):
        try:
            yield execution
        finally:
            current = ResearchRun.objects.filter(pk=run.pk).values("status").first()
            status = "completed" if execution.completed else "interrupted"
            if not execution.completed and current and current["status"] == "failed":
                status = "failed"
            ResearchStep.objects.filter(run_id=run.pk, execution_token=token, status="running").update(
                status=status, finished_at=timezone.now(), detail="" if status == "completed" else "执行已中断，恢复前会重新核对权限和版本。")


def task_summary(run, *, details=False):
    end = (run.finished_at or run.updated_at) if run.status in {"completed", "failed", "paused"} else timezone.now()
    issue = input_issue(run, configuration=run.status != "completed")
    steps = list(run.steps.all())
    calls = sorted(run.model_requests.all(), key=lambda call: call.created_at)
    if run.kind == "workflow":
        from .workflow import budget_available, call_records
        calls = call_records(run)
    resumable = bool(run.agent_snapshot or run.runtime_snapshot) and not issue and run.status in {"paused", "failed"} and run.attempts < 3
    if run.agent_snapshot:
        resumable = resumable and run.usage.get("calls", 0) < run.agent_snapshot["max_calls"] and run.usage.get("seconds", 0) < run.agent_snapshot["max_seconds"]
    if run.kind == "workflow":
        resumable = resumable and budget_available(run)
    payload = {"id": str(run.pk), "kind": run.kind, "kind_label": KINDS.get(run.kind, "资料处理"),
        "subject": run.profile.organization.name if run.profile_id else run.inputs.get("name", "未关联企业"),
        "quota_category": run.quota_category, "maintenance_reason": run.maintenance_reason,
        "status": run.status, "status_label": "需基于最新资料重建" if issue else STATUSES.get(run.status, "待核对"),
        "stage": "任务依据已变化" if issue else run.stage, "message": issue or run.error,
        "created_at": run.created_at, "finished_at": run.finished_at,
        "elapsed_seconds": max(0, (end - run.created_at).total_seconds()),
        "retry_at": run.retry_at,
        "completed_steps": sum(s.status == "completed" for s in steps), "recorded_steps": len(steps),
        "request_count": len(calls), "can_stop": not run.parent_id and run.status in {"queued", "running", "waiting"},
        "can_resume": resumable, "can_open": not issue,
        "resume_label": "继续未完成的分析" if run.kind == "workflow" else "从保存进度继续" if run.agent_snapshot else "重新执行此任务"}
    if run.parent_id:
        payload["can_resume"] = False
    if run.kind == "workflow":
        from .workflow import report
        payload["workflow"] = report(run)
    if details:
        payload["steps"] = [] if issue else [{"id": str(s.pk), "sequence": s.sequence, "label": s.label,
            "status": s.status if s.status != "running" or run.status == "running" else "interrupted",
            "detail": s.detail, "created_at": s.created_at, "finished_at": s.finished_at} for s in steps]
        payload["requests"] = [{"id": str(c.pk), "step_id": str(c.step_id) if c.step_id else None,
            "purpose": c.purpose, "model": c.model, "status": c.status, "duration_ms": c.duration_ms,
            "input_tokens": c.input_tokens, "output_tokens": c.output_tokens, "cost": c.estimated_cost,
            "currency": c.currency, "created_at": c.created_at} for c in calls]
    return payload
