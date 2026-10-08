"""Durable orchestration of confirmed profiles, retrieval and bounded analyses.

The coordinator never waits inside a worker for its children. Database checkpoints
and the existing dispatcher recover missed wake-ups; child graphs retain their own
evidence validation and lease ownership.
"""
import hashlib
import json
from datetime import timedelta

from core.ai_runtime import get_ai_profile
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from policies.catalog import formal_policies
from rest_framework.exceptions import ValidationError

from .agent import Halted
from .matching import match_policies
from .models import ResearchRun
from .policy_evidence import policy_signature
from .scoped_settings import research_configuration

ACTIVE = {"queued", "running", "waiting"}
VERSION = "matching-workflow-v2"
OPTIONS = ("gap_fill", "concurrency", "cache_hours", "retry_limit", "daily_calls")


def start(user, profile, *, project=None, view="opportunities", filters=None, source_run=None, fill_gaps=True, maintenance_actor=None, maintenance_reason=""):
    from accounts.permissions import can_manage_system
    from rest_framework.exceptions import PermissionDenied

    from .quota import ensure_available, record_maintenance
    from .runtime import snapshot
    from .tasks import enqueue

    if maintenance_actor and not can_manage_system(maintenance_actor):
        raise PermissionDenied("只有系统管理员可以发起维护重跑。")
    with transaction.atomic():
        get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
        profile.refresh_from_db()
        if not user.is_active or not profile.organization.membership_set.filter(user=user, active=True).exists():
            raise ValidationError("当前账号不能访问这家企业。")
        if not profile.confirmed_at:
            raise ValidationError("请先核对并确认企业画像，再启动自动匹配。")
        if project:
            project.refresh_from_db()
            if project.profile_id != profile.pk:
                raise ValidationError("请选择当前企业的项目。")
        if not get_ai_profile("enterprise_match").configured:
            raise ValidationError("请先配置企业画像与匹配使用的 AI 模型；仍可查看普通匹配结果。")
        config = research_configuration(user, profile)
        if not config.matching_allowed:
            raise ValidationError("当前企业暂未开放自动匹配，请联系管理员；已有匹配结果仍可查看。")
        saved = snapshot(profile, config, kind="workflow")
        saved.update(workflow_version=VERSION, max_policies=config.workflow_max_policies,
                     max_calls=config.workflow_max_calls, max_seconds=config.workflow_max_seconds)
        saved.update({key: getattr(config, "workflow_" + key) for key in OPTIONS})
        inputs = {"profile_revision": profile.revision, "project_id": str(project.pk) if project else None,
                  "project_revision": project.revision if project else None, "matching_view": view,
                  "filters": filters or {}, "source_run_id": str(source_run) if source_run else None, "fill_gaps": fill_gaps}
        fingerprint = hashlib.sha256(json.dumps([inputs, saved], sort_keys=True, default=str).encode()).hexdigest()
        roots = ResearchRun.objects.filter(user=user, parent__isnull=True)
        cached = roots.filter(kind="workflow", fingerprint=fingerprint, quota_category="maintenance" if maintenance_actor else "normal",
                              created_at__gte=timezone.now() - timedelta(hours=24), status__in=ACTIVE).first()
        if cached:
            return cached
        if roots.filter(status__in=ACTIVE).count() >= config.active_limit:
            raise ValidationError("已达到当前账号的同时处理任务数，请稍后启动自动匹配。")
        ensure_available(user, config.daily_limit, maintenance=bool(maintenance_actor))
        run = ResearchRun.objects.create(user=user, profile=profile, kind="workflow", inputs=inputs,
            fingerprint=fingerprint, runtime_snapshot=saved, stage="等待检索已发布政策")
        if maintenance_actor:
            record_maintenance(run, maintenance_actor, maintenance_reason)
        enqueue(run)
        return run


def input_issue(run, *, configuration=False):
    if run.runtime_snapshot.get("workflow_version") not in {VERSION, "matching-workflow-v1"}:
        return "自动匹配流程版本已更新，请重新启动。"
    if not run.profile_id or not run.profile.confirmed_at:
        return "企业画像尚未确认。"
    if configuration:
        config = research_configuration(run.user, run.profile)
        if not config or any(run.runtime_snapshot.get(key) != getattr(config, "workflow_" + key) for key in ("max_policies", "max_calls", "max_seconds")):
            return "自动匹配预算配置已变化，请重新启动。"
        if run.runtime_snapshot.get("workflow_version") == VERSION and any(run.runtime_snapshot.get(key) != getattr(config, "workflow_" + key) for key in OPTIONS):
            return "自动补查或执行配置已变化，请重新启动。"
    if run.inputs.get("project_id"):
        project = run.profile.projects.filter(pk=run.inputs["project_id"]).first()
        if not project or project.revision != run.inputs.get("project_revision"):
            return "项目已修改或删除，请基于最新项目重新匹配。"
    return ""


def parent_guard(child):
    from .runtime import input_issue as runtime_issue
    parent = ResearchRun.objects.select_related("profile", "user").get(pk=child.parent_id)
    if parent.status not in ACTIVE:
        raise Halted("主任务已停止，本次结果不会应用。")
    issue = runtime_issue(parent, configuration=True)
    if issue:
        raise Halted(issue)


def reserve_request(child, token):
    """Persist a reservation before HTTP, including attempts lost to worker crashes."""
    with transaction.atomic():
        get_user_model().objects.select_for_update(no_key=True).get(pk=child.user_id)
        parent = ResearchRun.objects.select_for_update().get(pk=child.parent_id)
        parent_guard(child)
        if not ResearchRun.objects.filter(pk=child.pk, status="running", lease_token=token).exists():
            raise Halted("任务已停止，未继续调用模型。")
        usage = dict(parent.usage)
        if usage.get("calls", 0) >= parent.runtime_snapshot["max_calls"]:
            raise Halted("本次自动匹配的模型请求预算已用完，可查看已有结果。")
        if spent_seconds(parent) >= parent.runtime_snapshot["max_seconds"]:
            raise Halted("本次自动匹配的执行时间预算已用完，可查看已有结果。")
        today = timezone.localdate().isoformat()
        daily = sum(row.get("daily_calls", 0) for row in ResearchRun.objects.filter(user=child.user, kind="workflow", quota_category=parent.quota_category, usage__request_day=today).values_list("usage", flat=True))
        if daily >= parent.runtime_snapshot.get("daily_calls", 40):
            raise Halted("今天的自动匹配模型调用额度已用完，可在明天继续或查看已有结果。")
        usage["daily_calls"] = (usage.get("daily_calls", 0) if usage.get("request_day") == today else 0) + 1
        usage["request_day"] = today
        usage["calls"] = usage.get("calls", 0) + 1
        parent.usage = usage
        parent.save(update_fields=["usage", "updated_at"])


def spent_seconds(parent):
    total = 0
    for child in parent.children.all():
        total += child.usage.get("workflow_seconds", 0)
        if child.status == "running" and child.started_at:
            total += max(0, (timezone.now() - child.started_at).total_seconds())
    return total


def budget_available(parent):
    return parent.usage.get("calls", 0) < parent.runtime_snapshot.get("max_calls", 0) and spent_seconds(parent) < parent.runtime_snapshot.get("max_seconds", 0)


def reuse(child, parent):
    """Only reuse this user's exact, still valid policy/profile/config analysis."""
    from .runtime import input_issue as runtime_issue
    hours = parent.runtime_snapshot.get("cache_hours", 0)
    if not hours or child.kind != "explanation":
        return False
    candidates = ResearchRun.objects.filter(user=child.user, profile=child.profile, kind="explanation", status="completed",
        finished_at__gte=timezone.now() - timedelta(hours=hours), inputs__policy_id=child.inputs["policy_id"]).exclude(pk=child.pk).order_by("-finished_at")[:20]
    keys = ("policy_id", "policy_version", "profile_revision", "project_id", "project_revision", "matching_signature", "matching_view")
    for cached in candidates:
        if (cached.result and not cached.checkpoint.get("reused_from") and
            all(cached.inputs.get(key) == child.inputs.get(key) for key in keys) and
            cached.runtime_snapshot.get("signature") == child.runtime_snapshot.get("signature") and
            cached.runtime_snapshot.get("version") == child.runtime_snapshot.get("version") and not runtime_issue(cached, configuration=True)):
            if cached.runtime_snapshot.get("analysis_version") != child.runtime_snapshot.get("analysis_version"):
                continue
            child.result, child.status, child.finished_at = cached.result, "completed", timezone.now()
            child.checkpoint = {"reused_from": str(cached.pk)}
            child.stage = "已复用版本一致且仍有效的解读"
            child.save()
            return True
    return False


def retry_transient(run, token, exc):
    import httpx
    from core.ai_capacity import ModelCapacityBusy

    transient = isinstance(exc, (ModelCapacityBusy, httpx.TimeoutException, httpx.NetworkError)) or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {429, 502, 503, 504})
    if not transient:
        return False
    with transaction.atomic():
        parent = ResearchRun.objects.select_for_update().get(pk=run.parent_id)
        current = ResearchRun.objects.select_for_update().get(pk=run.pk)
        retries = current.usage.get("auto_retries", 0)
        if parent.status not in ACTIVE or current.status != "running" or current.lease_token != token or retries >= parent.runtime_snapshot.get("retry_limit", 0) or not budget_available(parent):
            return False
        current.usage["workflow_seconds"] = current.usage.get("workflow_seconds", 0) + max(0, (timezone.now() - current.started_at).total_seconds())
        current.usage["auto_retries"] = retries + 1
        current.status, current.started_at, current.lease_token = "queued", None, None
        current.retry_at = timezone.now() + timedelta(seconds=30 * 2**retries)
        current.stage = "外部服务暂时繁忙，稍后自动重试"
        current.error = ""
        current.save()
    return True


def advance(run, execution):
    """One coordinator tick: snapshot retrieval, dispatch one child, or summarize."""
    from .runtime import input_issue as runtime_issue
    from .tasks import enqueue

    execution.guard()
    checkpoint = run.checkpoint
    if not checkpoint.get("retrieved"):
        execution.progress("正在检索政策并核对明确条件")
        project = run.profile.projects.get(pk=run.inputs["project_id"]) if run.inputs.get("project_id") else None
        result = match_policies(run.user, run.profile, project, run.inputs["matching_view"], run.inputs["filters"])
        filtered = [row for row in result["items"] if not run.inputs["filters"].get("level") or row["level"] == run.inputs["filters"]["level"]]
        rows = [row for row in filtered if row["level"] in {"high", "medium"} and row["conditions"]["status"] != "conflict"][:run.runtime_snapshot["max_policies"]]
        candidates = []
        for row in rows:
            policy = formal_policies(run.user).filter(pk=row["policy_id"], version=row["policy_version"]).first()
            if policy:
                candidates.append({"policy_id": str(policy.pk), "policy_version": policy.version,
                                   "matching_signature": policy_signature(run.user, policy)})
        checkpoint = {"retrieved": True, "candidate_count": len(filtered), "selected": candidates,
                      "conflicting_policies": result["conflicting_policies"], "search_backend": result["search_backend"]}
        from .gap_fill import gaps
        checkpoint["gaps"] = gaps(run.profile, project, rows)
        execution.guard()
    with transaction.atomic():
        current = ResearchRun.objects.select_for_update().get(pk=run.pk)
        if current.status != "running" or current.lease_token != execution.token:
            raise Halted("主任务已停止。")
        execution.guard()
        current.checkpoint = checkpoint
        current.save(update_fields=["checkpoint", "updated_at"])
        if "gap_checked" not in current.checkpoint:
            from .gap_fill import create_child
            if run.runtime_snapshot.get("gap_fill") and run.inputs.get("fill_gaps", True):
                _, note = create_child(current, checkpoint.get("gaps", []))
            else:
                note = "本次不自动补查资料，可手动完善画像。"
            current.checkpoint.update(gap_checked=True, gap_note=note)
            current.save(update_fields=["checkpoint", "updated_at"])
        for row in checkpoint["selected"]:
            current.children.get_or_create(fingerprint=row["policy_id"], defaults={
                "user": run.user, "profile": run.profile, "kind": "explanation", "status": "pending",
                "runtime_snapshot": run.runtime_snapshot,
                "inputs": {**row, "project_id": run.inputs.get("project_id"),
                           "project_revision": run.inputs.get("project_revision"),
                           "profile_revision": run.inputs["profile_revision"], "matching_view": run.inputs["matching_view"]}})
        children = list(current.children.order_by("created_at", "pk"))
        active_count = sum(child.status in {"queued", "running"} for child in children)
        gap = next((child for child in children if child.kind == "company" and child.status in {"pending", "queued", "running"}), None)
        slots = max(0, (1 if gap else current.runtime_snapshot.get("concurrency", 1)) - active_count)
        if slots:
            for child in children:
                if child.status != "pending":
                    continue
                if gap and child.pk != gap.pk:
                    continue
                issue = runtime_issue(child, configuration=True)
                if not issue and reuse(child, current):
                    continue
                if issue or not budget_available(current):
                    child.status, child.error = "failed", issue or "本次自动匹配预算已用完，尚未解读此政策。"
                    child.stage, child.finished_at = "未完成解读", timezone.now()
                    child.save()
                    continue
                child.status, child.stage = "queued", "等待政策条件分析"
                child.save(update_fields=["status", "stage", "updated_at"])
                enqueue(child)
                active_count += 1
                slots -= 1
                if not slots:
                    break
        current.lease_token = None
        if active_count:
            current.status = "waiting"
            current.stage = "正在按资料缺口补查，结果待确认" if gap else f"正在解读相关政策，已完成 {sum(c.status == 'completed' for c in children if c.kind == 'explanation')}/{sum(c.kind == 'explanation' for c in children)} 份"
        else:
            failures = sum(c.status != "completed" for c in children)
            current.status = "failed" if failures else "completed"
            current.stage = "部分政策未完成，可查看结果或继续" if failures else "已完成自动匹配" if children else "检索完成，暂无适合自动解读的相关政策"
            current.error = "部分解读未完成；已完成的结果保留，继续前会核对版本和剩余预算。" if failures else ""
            current.finished_at = timezone.now()
        current.save()
        execution.completed = True


def stop(run):
    """Caller holds the parent's row lock; invalidate all unfinished child leases."""
    for child in run.children.filter(status__in=ACTIVE | {"pending"}):
        seconds = child.usage.get("workflow_seconds", 0)
        if child.status == "running" and child.started_at:
            seconds += max(0, (timezone.now() - child.started_at).total_seconds())
        child.usage["workflow_seconds"] = seconds
        child.status, child.lease_token, child.stage = "paused", None, "主任务已停止"
        child.finished_at = timezone.now()
        child.save()


def prepare_resume(run):
    if not budget_available(run):
        raise ValidationError("本次自动匹配预算已用完，请查看已有结果或重新启动匹配。")
    # Never rerun successful analysis. The coordinator revalidates pending children.
    run.children.filter(status__in=["failed", "paused"]).update(status="pending", error="", started_at=None, finished_at=None, lease_token=None)


def wake(parent_id):
    from .tasks import enqueue
    with transaction.atomic():
        parent = ResearchRun.objects.select_for_update().filter(pk=parent_id, status="waiting").first()
        if parent and (not parent.children.filter(status__in=["queued", "running"]).exists() or (
            parent.children.filter(status="pending").exists() and
            not parent.children.filter(kind="company", status__in=["pending", "queued", "running"]).exists() and
            parent.children.filter(status__in=["queued", "running"]).count() < parent.runtime_snapshot.get("concurrency", 1))):
            parent.status, parent.started_at = "queued", None
            parent.save(update_fields=["status", "started_at", "updated_at"])
            enqueue(parent)


def report(run):
    from .runtime import input_issue as runtime_issue
    problem = runtime_issue(run)
    items = []
    gap_info = {"fields": run.checkpoint.get("gaps", []), "note": run.checkpoint.get("gap_note", ""), "run_id": None, "status": "", "can_open": False}
    for child in run.children.select_related("user", "profile").order_by("created_at", "pk"):
        issue = problem or runtime_issue(child)
        if child.kind == "company":
            gap_info.update(run_id=str(child.pk), status=child.status, note=issue or child.error or child.stage,
                            can_open=not issue and child.status == "completed" and bool(child.result.get("candidates")))
            continue
        policy = formal_policies(run.user).filter(pk=child.inputs.get("policy_id")).first() if not issue else None
        items.append({"id": str(child.pk), "policy_id": str(policy.pk) if policy else None,
                      "title": policy.title if policy else "政策依据已变化",
                      "status": child.status, "stage": issue or child.error or child.stage,
                      "can_open": not issue and child.status == "completed", "reused": bool(child.checkpoint.get("reused_from"))})
    return {"items": items, "selected_count": len(items), "completed_count": sum(i["can_open"] for i in items),
            "candidate_count": run.checkpoint.get("candidate_count", 0), "retrieved": bool(run.checkpoint.get("retrieved")),
            "calls": run.usage.get("calls", 0), "max_calls": run.runtime_snapshot.get("max_calls", 0),
            "gap_fill": gap_info, "reused_count": sum(i["reused"] for i in items),
            "concurrency": run.runtime_snapshot.get("concurrency", 1),
            "max_policies": run.runtime_snapshot["max_policies"], "source_run_id": run.inputs.get("source_run_id"),
            "notice": problem or f"使用已确认画像检索真实政策库，按当前排序最多解读{run.runtime_snapshot['max_policies']}份高／中相关且无明确条件冲突的政策；不作资格认证。画像和项目不会被自动改写，订阅仍由你选择。"}


def call_records(run):
    from core.models import AICall
    return list(AICall.objects.filter(Q(task=run) | Q(task__parent=run)).order_by("created_at"))
