import hashlib
import json
import uuid
from datetime import timedelta

import httpx
from celery import shared_task
from core.ai_runtime import get_ai_profile
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from policies.catalog import formal_policies

from .match_analysis import explain_match
from .matching import INACTIVE
from .materials import MaterialError
from .models import EnterpriseProfile, ResearchRun, ResearchSettings
from .policy_evidence import has_active_opportunity, policy_signature
from .research import ResearchUnavailable, company_graph, project_draft
from .runtime import snapshot, tracking
from .scoped_settings import research_configuration
from .watch_tasks import dispatch_watches, maintain_watch_history, process_watch  # noqa: F401


def failure_message(exc):
    from core.ai_capacity import ModelCapacityBusy
    if isinstance(exc, ModelCapacityBusy):
        return str(exc)
    if isinstance(exc, (ResearchUnavailable, MaterialError)):
        return str(exc)
    if isinstance(exc, httpx.TimeoutException):
        return "搜索或模型服务响应超时，请稍后重试。现有画像不受影响。"
    if isinstance(exc, httpx.HTTPStatusError):
        return "搜索或模型服务拒绝了请求，请在系统配置中检查密钥、额度及模型名称。"
    if isinstance(exc, httpx.RequestError):
        return "无法连接搜索或模型服务，请检查网络及服务地址。"
    if str(exc) == "AI_NOT_CONFIGURED":
        return "企业画像所用模型尚未配置，请在系统配置的 AI 模型中设置。"
    return "本次资料整理未能完成或引用不足，请重试、补充信息或手动完善画像。"


@shared_task(time_limit=600, soft_time_limit=570)
def process_research(run_id):
    token = uuid.uuid4()
    if not ResearchRun.objects.filter(pk=run_id, status="queued").filter(Q(retry_at__isnull=True) | Q(retry_at__lte=timezone.now())).update(
        status="running", lease_token=token, started_at=timezone.now(), stage="开始整理资料"
    ):
        return
    run = ResearchRun.objects.select_related("user", "profile").get(pk=run_id)
    with tracking(run, token) as execution:
        owner = ResearchRun.objects.filter(pk=run_id, status="running", lease_token=token)
        if run.agent_snapshot:
            run.usage["attempt_base_seconds"] = run.usage.get("seconds", 0)
            owner.update(usage=run.usage)
        runner = None
        try:
            execution.guard()
            if run.kind == "workflow":
                from .workflow import advance
                advance(run, execution)
                return
            if not run.user.is_active:
                raise ResearchUnavailable("账号已停用，任务未继续处理。")
            if run.profile and not run.profile.organization.membership_set.filter(user=run.user, active=True).exists():
                raise ResearchUnavailable("企业成员权限已经变化，请刷新后重试。")
            if run.kind == "company":
                if run.agent_snapshot:
                    from .agent import Runner
                    runner = Runner(run, token)
                    result = runner.execute()
                else:
                    result = company_graph(run.inputs, execution.progress, run.uploaded_material, config=research_configuration(run.user, run.profile))
            elif run.kind == "project":
                execution.progress("正在整理项目名称和标签")
                result = project_draft(run.inputs)
            else:
                execution.progress("核对画像与政策版本")
                policy = formal_policies(run.user).get(pk=run.inputs["policy_id"])
                project = run.profile.projects.get(pk=run.inputs["project_id"]) if run.inputs.get("project_id") else None
                if run.profile.revision != run.inputs["profile_revision"] or policy.version != run.inputs["policy_version"] or (project and project.revision != run.inputs["project_revision"]):
                    raise ResearchUnavailable("画像、项目或政策已经更新，请重新生成解读。")
                def guard_analysis():
                    run.user.refresh_from_db()
                    run.profile.refresh_from_db()
                    if project:
                        project.refresh_from_db()
                    latest = formal_policies(run.user).filter(pk=policy.pk).first()
                    if not owner.exists() or not run.user.is_active or not run.profile.organization.membership_set.filter(user=run.user, active=True).exists():
                        raise ResearchUnavailable("任务已停止或企业访问权限已变化。")
                    if not latest or latest.version != run.inputs["policy_version"] or run.profile.revision != run.inputs["profile_revision"] or (project and project.revision != run.inputs["project_revision"]) or (run.inputs.get("matching_signature") and policy_signature(run.user, latest) != run.inputs["matching_signature"]):
                        raise ResearchUnavailable("政策、批次、资料或画像已更新，请重新分析。")
                    if latest.validity_status in INACTIVE or (run.inputs.get("matching_view") == "opportunities" and not has_active_opportunity(run.user, latest)):
                        raise ResearchUnavailable("政策或申报窗口已变化，请刷新匹配结果。")
                result = explain_match(run.user, run.profile, project, policy,
                                       stage=execution.progress, guard=guard_analysis)
                guard_analysis()
            execution.guard()
            execution.completed = bool(owner.update(status="completed", stage="已完成，请核对结果", result=result,
                         error="", finished_at=timezone.now(), lease_token=None))
        except Exception as exc:
            from core.ai_capacity import ModelCapacityBusy

            from .agent import Halted
            if runner and owner.exists():
                try:
                    runner.persist()
                except Halted:
                    return
            if isinstance(exc, Halted):
                owner.update(status="paused", stage="已停止，可核对后继续", error=str(exc), lease_token=None)
                return
            if run.parent_id:
                from .workflow import retry_transient
                if retry_transient(run, token, exc):
                    return
            if not run.parent_id and runner and isinstance(exc, ModelCapacityBusy) and run.attempts < 3:
                owner.update(status="queued", started_at=None, lease_token=None, retry_at=timezone.now() + timedelta(seconds=30), attempts=run.attempts + 1, stage="等待模型空闲后继续")
                return
            owner.update(status="failed", stage="需要重试或补充信息", error=failure_message(exc),
                         finished_at=timezone.now(), lease_token=None)
        finally:
            if run.parent_id:
                from .workflow import wake
                with transaction.atomic():
                    latest = ResearchRun.objects.select_for_update().get(pk=run.pk)
                    if latest.started_at == run.started_at and latest.status in {"completed", "failed", "paused"}:
                        latest.usage["workflow_seconds"] = max(latest.usage.get("workflow_seconds", 0), run.usage.get("workflow_seconds", 0) + max(0, (timezone.now() - run.started_at).total_seconds()))
                        latest.save(update_fields=["usage", "updated_at"])
                wake(run.parent_id)


@shared_task
def dispatch_research():
    # Recover abandoned jobs without allowing a stale worker to overwrite a retry.
    stale = ResearchRun.objects.filter(status="running", started_at__lt=timezone.now() - timedelta(minutes=12))
    for child in stale.filter(parent__isnull=False):
        usage = dict(child.usage)
        usage["workflow_seconds"] = usage.get("workflow_seconds", 0) + max(0, (timezone.now() - child.started_at).total_seconds())
        stale.filter(pk=child.pk, lease_token=child.lease_token).update(usage=usage)
    for abandoned in stale.exclude(agent_snapshot={}):
        usage = dict(abandoned.usage)
        usage["seconds"] = max(usage.get("seconds", 0), abandoned.agent_snapshot.get("max_seconds", 480))
        stale.filter(pk=abandoned.pk, lease_token=abandoned.lease_token).update(usage=usage)
    stale.update(status="failed", stage="处理超时", error="任务处理时间过长或服务曾中断，请重试；现有画像未被修改。", lease_token=None)
    from .runtime import input_issue
    from .workflow import stop, wake
    for root in ResearchRun.objects.filter(kind="workflow", status="waiting")[:20]:
        issue = input_issue(root, configuration=True)
        if issue:
            with transaction.atomic():
                root = ResearchRun.objects.select_for_update().get(pk=root.pk)
                if root.status != "waiting":
                    continue
                root.status, root.error, root.lease_token = "paused", issue, None
                root.save()
                stop(root)
        else:
            wake(root.pk)
    due = ResearchRun.objects.filter(status="queued").filter(
        Q(started_at__isnull=True) | Q(started_at__lt=timezone.now() - timedelta(minutes=12))
    ).filter(Q(retry_at__isnull=True) | Q(retry_at__lte=timezone.now()))
    for run in due[:10]:
        enqueue(run)
    schedule_refreshes()


def schedule_refreshes():
    config, _ = ResearchSettings.objects.get_or_create(key="default")
    if not get_ai_profile("enterprise").configured:
        return
    due = EnterpriseProfile.objects.filter(refresh_days__gt=0, next_research_at__lte=timezone.now())
    for profile in due.select_related("organization")[:10]:
        if profile.research_method == "materials":
            continue
        if profile.research_method == "search" and not config.search_ready:
            continue
        if profile.research_method == "website" and not profile.data.get("website"):
            continue
        membership = profile.organization.membership_set.filter(active=True, role="admin", user__is_active=True).order_by("created_at").first()
        if not membership:
            continue
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=membership.user_id)
            profile = EnterpriseProfile.objects.select_for_update().get(pk=profile.pk)
            if not profile.refresh_days or not profile.next_research_at or profile.next_research_at > timezone.now():
                continue
            if profile.research_method not in {"search", "website"}:
                continue
            if profile.research_method == "search" and not config.search_ready:
                continue
            if profile.research_method == "website" and not profile.data.get("website"):
                continue
            scoped = research_configuration(membership.user, profile, base=config)
            if not scoped.research_allowed:
                continue
            # Both manual and scheduled work share the same quota.
            runs = ResearchRun.objects.filter(user_id=membership.user_id, parent__isnull=True)
            from rest_framework.exceptions import ValidationError

            from .quota import ensure_available

            if runs.filter(status__in=["queued", "running", "waiting"]).count() >= scoped.active_limit:
                continue
            try:
                ensure_available(membership.user, scoped.daily_limit)
            except ValidationError:
                continue
            inputs = {"name": profile.organization.name, "profile_id": str(profile.pk), "source_mode": profile.research_method}
            inputs.update({key: profile.data.get(key, "") for key in ("city", "credit_code", "website")})
            from .grounding import VALIDATION_VERSION

            fingerprint = hashlib.sha256(json.dumps([VALIDATION_VERSION, "company", inputs], sort_keys=True).encode()).hexdigest()
            run = ResearchRun.objects.create(user_id=membership.user_id, profile=profile, inputs=inputs, fingerprint=fingerprint, runtime_snapshot=snapshot(profile, scoped))
            from .agent import snapshot_for
            run.agent_snapshot = snapshot_for(profile, user=membership.user)
            run.save(update_fields=["agent_snapshot"])
            profile.next_research_at = timezone.now() + timedelta(days=profile.refresh_days)
            profile.save(update_fields=["next_research_at"])
            enqueue(run)


def enqueue(run):
    def dispatch():
        ResearchRun.objects.filter(pk=run.pk, status="queued").update(started_at=timezone.now())
        try:
            process_research.delay(str(run.pk))
        except Exception:
            # Beat retries queued jobs after broker recovery.
            ResearchRun.objects.filter(pk=run.pk, status="queued").update(stage="等待后台任务服务恢复", started_at=None)
    transaction.on_commit(dispatch)
