"""Opt-in, bounded incremental matching; reuse evidence and in-app delivery."""

import uuid
from datetime import timedelta

from accounts.models import Membership, User
from accounts.services import access_decision
from core.ai_runtime import get_ai_profile, profile_signature
from core.business_config import checksum, config_version
from core.models import AuditRecord
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from policies.catalog import formal_policies
from policies.models import Policy, PublicationEvent

from .match_analysis import explain_match
from .matching import INACTIVE, match_policies
from .models import (
    EnterpriseProfile,
    EnterpriseProject,
    PolicyRecommendation,
    PolicyWatch,
    RecommendationSettings,
    WatchRun,
)
from .policy_evidence import has_active_opportunity, policy_signature
from .scoped_settings import recommendation_configuration


class WatchStopped(Exception):
    pass


def configuration(profile=None, *, base=None):
    if profile is None and base is None:
        return RecommendationSettings.objects.get_or_create(key="default")[0]
    return recommendation_configuration(profile, base=base)


def permitted(watch, config=None):
    config = config or configuration(watch.profile)
    return bool(
        config.enabled
        and watch.enabled
        and watch.user.is_active
        and (config.all_organizations or str(watch.profile.organization_id) in config.organizations)
        and Membership.objects.filter(
            user=watch.user, organization_id=watch.profile.organization_id, active=True
        ).exists()
        and all(
            access_decision(watch.user, capability)["allowed"]
            for capability in ["policy_detail", "policy_subscription"]
        )
    )


def input_signature(watch):
    model = get_ai_profile("enterprise_match")
    model_identity = [model.base_url, model.model, model.enabled]
    if model.thinking or model.context_tokens or model.max_output_tokens:
        model_identity.append(profile_signature(model))
    return checksum(
        {
            "profile": watch.profile.data,
            "revision": watch.profile.revision,
            "project": [watch.project.data, watch.project.description, watch.project.revision]
            if watch.project_id
            else None,
            "view": watch.view,
            "ai": watch.ai_explanations,
            "config": config_version(),
            "model": model_identity
            if watch.ai_explanations
            else None,
        }
    )


def run_signature(watch, config):
    values = [input_signature(watch), watch.consent_version, config.updated_at.isoformat()]
    if getattr(config, "scope_overrides", {}):
        values.append(config.scope_overrides)
    return checksum(values)


def start_due_runs():
    platform = configuration()
    if not platform.enabled:
        return 0
    now, count = timezone.now(), 0
    for watch_id in (
        PolicyWatch.objects.filter(enabled=True).values_list("pk", flat=True).iterator()
    ):
        with transaction.atomic():
            watch = (
                PolicyWatch.objects.select_for_update(of=("self",))
                .select_related("user", "profile", "project")
                .get(pk=watch_id)
            )
            config = configuration(watch.profile, base=platform)
            if not permitted(watch, config):
                PolicyWatch.objects.filter(pk=watch.pk).update(
                    message="当前企业范围或账号权限不允许持续匹配，已暂停。"
                )
                continue
            signature = input_signature(watch)
            changed = watch.input_signature != signature
            if watch.runs.filter(status__in=["queued", "running"]).exists():
                continue
            edited_at = max(
                watch.updated_at,
                watch.profile.updated_at,
                watch.project.updated_at if watch.project_id else watch.profile.updated_at,
            )
            if changed and edited_at + timedelta(minutes=config.aggregation_minutes) > now:
                continue
            if watch.next_run_at and watch.next_run_at > now and not changed:
                continue
            full = (
                changed
                or not watch.last_full_scan_at
                or watch.last_full_scan_at <= now - timedelta(days=1)
            )
            WatchRun.objects.create(
                watch=watch,
                signature=run_signature(watch, config),
                cutoff=now,
                full_scan=full,
                notify_changes=watch.has_baseline,
            )
            count += 1
    return count


def visible_recommendations(user):
    qs = PolicyRecommendation.objects.filter(
        watch__user=user,
        active=True,
        watch__enabled=True,
        watch__profile__organization__membership__user=user,
        watch__profile__organization__membership__active=True,
        profile_revision=F("watch__profile__revision"),
        policy_version=F("policy__version"),
        policy__in=formal_policies(user),
    ).filter(Q(watch__project__isnull=True) | Q(project_revision=F("watch__project__revision")))
    return qs.filter(
        Q(result__change_only=True) | ~Q(policy__validity_status__in=INACTIVE)
    ).distinct()


def recommendation_is_current(item):
    watch = item.watch
    return bool(
        permitted(watch)
        and item.active
        and item.profile_revision == watch.profile.revision
        and item.project_revision == (watch.project.revision if watch.project_id else None)
        and formal_policies(watch.user).filter(pk=item.policy_id).exists()
        and (item.result.get("change_only") or item.policy.validity_status not in INACTIVE)
        and item.result.get("input_signature") == input_signature(watch)
        and item.result.get("policy_signature") == policy_signature(watch.user, item.policy)
        and (
            item.result.get("change_only")
            or watch.view != "opportunities"
            or has_active_opportunity(watch.user, item.policy)
        )
    )


def deliver_recommendation(item):
    from subscriptions.delivery import preferences
    from subscriptions.models import Notification, PendingDelivery

    if not recommendation_is_current(item):
        return 0
    reasons = item.result["reasons"] + [item.result["recommendation_label"]]
    if preferences(item.watch.user_id).update_mode == "daily" and not item.result.get("important"):
        return int(
            PendingDelivery.objects.get_or_create(
                user=item.watch.user, recommendation=item, defaults={"reasons": reasons}
            )[1]
        )
    return int(
        Notification.objects.get_or_create(
            user=item.watch.user,
            delivery_key=f"recommendation:{item.pk}",
            defaults={
                "recommendation": item,
                "kind": "recommendation",
                "title": ("关注变化：" if item.result.get("change_only") else "持续匹配：")
                + item.policy.title[:490],
                "reasons": reasons,
            },
        )[1]
    )


def reserve_model_call(run, config):
    if run.model_calls >= config.model_calls_per_run:
        return False
    with transaction.atomic():
        platform = RecommendationSettings.objects.select_for_update().get(pk=config.pk)
        fresh = WatchRun.objects.select_for_update().get(pk=run.pk)
        if (
            fresh.status != "running"
            or fresh.lease_token != run.lease_token
            or fresh.model_calls >= config.model_calls_per_run
        ):
            return False
        count = AuditRecord.objects.filter(
            action="watch.model_reserved", created_at__date=timezone.localdate()
        ).count()
        enterprise_count = enterprise_usage("watch.model_reserved", run.watch.profile_id).count()
        if count >= platform.daily_model_calls or enterprise_count >= config.daily_model_calls:
            return False
        AuditRecord.objects.create(
            action="watch.model_reserved", object_id=run.pk, details={"purpose": "enterprise", "profile_id": str(run.watch.profile_id)}
        )
        WatchRun.objects.filter(pk=run.pk).update(model_calls=F("model_calls") + 1)
        run.model_calls = fresh.model_calls + 1
    return True


def process_batch(run_id):
    token, now = uuid.uuid4(), timezone.now()
    run = WatchRun.objects.get(pk=run_id)
    with transaction.atomic():
        watch = (
            PolicyWatch.objects.select_for_update(of=("self",))
            .select_related("user", "profile", "project")
            .get(pk=run.watch_id)
        )
        platform = RecommendationSettings.objects.select_for_update().get(key="default")
        config = configuration(watch.profile, base=platform)
        run = WatchRun.objects.select_for_update().get(pk=run_id)
        if (
            run.status not in {"queued", "running"}
            or (run.lease_until and run.lease_until > now)
            or (run.retry_at and run.retry_at > now)
        ):
            return
        if not permitted(watch, config) or run.signature != run_signature(watch, config):
            run.status, run.message, run.finished_at = (
                "cancelled",
                "关注、画像或运行配置已变化，旧任务不再应用。",
                now,
            )
            run.save()
            return
        used = AuditRecord.objects.filter(
            action="watch.batch_started", created_at__date=timezone.localdate()
        ).count()
        if used >= platform.daily_batches or enterprise_usage("watch.batch_started", watch.profile_id).count() >= config.daily_batches:
            run.retry_at = timezone.localtime(now).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
            run.message = "今日处理额度已用完，等待次日继续。"
            run.save()
            return
        AuditRecord.objects.create(action="watch.batch_started", object_id=run.pk, details={"profile_id": str(watch.profile_id)})
        run.status, run.lease_token, run.lease_until = "running", token, now + timedelta(minutes=12)
        run.message = "正在检查变化并匹配已确认资料"
        run.save()

    owner = WatchRun.objects.filter(pk=run.pk, status="running", lease_token=token)

    def guard():
        fresh = PolicyWatch.objects.select_related("user", "profile", "project").get(pk=watch.pk)
        current_config = configuration(fresh.profile)
        if (
            not owner.exists()
            or not permitted(fresh, current_config)
            or run_signature(fresh, current_config) != run.signature
        ):
            raise WatchStopped("关注已关闭、权限或资料已变化，未应用迟到结果。")

    try:
        guard()
        if run.full_scan:
            query = Policy.objects.filter(
                Q(pk__in=formal_policies(watch.user))
                | Q(pk__in=watch.recommendations.values("policy_id"))
            )
        else:
            # Small overlap tolerates ordinary outbox transaction delays; a daily
            # complete scan repairs long delays and time-driven window changes.
            since = (watch.event_watermark or run.cutoff) - timedelta(minutes=5)
            events = PublicationEvent.objects.filter(
                created_at__gte=since, created_at__lte=run.cutoff
            )
            query = Policy.objects.filter(pk__in=events.values("policy_id"))
        query = query.filter(created_at__lte=run.cutoff)
        if run.cursor:
            query = query.filter(pk__gt=run.cursor)
        policies = list(query.order_by("pk")[: config.batch_size])
        proofs = {p.pk: policy_signature(watch.user, p) for p in policies}
        matches = {
            i["policy_id"]: i
            for i in match_policies(
                watch.user,
                watch.profile,
                watch.project,
                watch.view,
                policy_ids=[p.pk for p in policies],
            )["items"]
        }
        inputs = input_signature(watch)
        for policy in policies:
            guard()
            item = matches.get(str(policy.pk))
            proof = proofs[policy.pk]
            fingerprint = checksum([inputs, proof])
            existing = watch.recommendations.filter(policy=policy, fingerprint=fingerprint).first()
            analysis, analysis_message = None, "未开启自动 AI 解读，可在政策匹配中手动解读。"
            if existing and existing.result:
                analysis = existing.result.get("analysis")
                analysis_message = existing.result.get("analysis_message", analysis_message)
            relevant = item and item["level"] in {"high", "medium"}
            if relevant and not existing and watch.ai_explanations:
                if get_ai_profile("enterprise_match").configured and reserve_model_call(run, config):
                    try:
                        analysis = explain_match(
                            watch.user, watch.profile, watch.project, policy, guard=guard
                        )
                        analysis_message = analysis["notice"]
                    except WatchStopped:
                        raise
                    except Exception:
                        analysis_message = (
                            "AI 解读暂未完成，保留有依据的规则匹配；可在政策匹配中手动重试。"
                        )
                else:
                    analysis_message = "模型未配置或已达本次／今日 AI 额度，保留规则匹配结果。"
            with transaction.atomic():
                User.objects.select_for_update(no_key=True).get(pk=watch.user_id)
                EnterpriseProfile.objects.select_for_update().get(pk=watch.profile_id)
                if watch.project_id:
                    EnterpriseProject.objects.select_for_update().get(pk=watch.project_id)
                PolicyWatch.objects.select_for_update().get(pk=watch.pk)
                RecommendationSettings.objects.select_for_update().get(pk=config.pk)
                WatchRun.objects.select_for_update().get(pk=run.pk)
                current_policy = Policy.objects.select_for_update().get(pk=policy.pk)
                list(
                    Membership.objects.select_for_update().filter(
                        user=watch.user, organization_id=watch.profile.organization_id
                    )
                )
                guard()
                if policy_signature(watch.user, current_policy) != proof:
                    raise WatchStopped("政策内容或申报窗口已变化，待下一轮重新计算。")
                previous = watch.recommendations.filter(policy=policy, active=True).first()
                change_only = bool(
                    not relevant
                    and previous
                    and formal_policies(watch.user).filter(pk=policy.pk).exists()
                )
                if change_only:
                    reason = "与当前画像或项目不再有足够相关依据，请重新核对。"
                    if policy.validity_status in INACTIVE:
                        reason = "政策效力已调整为：" + policy.get_validity_status_display() + "。"
                    elif watch.view == "opportunities" and not has_active_opportunity(
                        watch.user, policy
                    ):
                        reason = "当前已无可用申报窗口，可能已截止、暂停或完成，请核对最新原文。"
                    item = {
                        "level_label": "状态变化",
                        "recommendation_label": "不再作为当前可申报推荐",
                        "level": "insufficient",
                        "recommendation_group": "changed",
                        "reasons": [reason],
                        "gaps": ["本条用于提醒已关注结果变化，不构成资格判断。"],
                        "change_only": True,
                        "important": True,
                    }
                    analysis, analysis_message = None, "状态变化依据来自当前库内政策与申报窗口。"
                if relevant or change_only:
                    if relevant and previous:
                        item["important"] = bool(
                            previous.result.get("deadline") != item.get("deadline")
                            or previous.result.get("opportunities") != item.get("opportunities")
                        )
                    if analysis and analysis.get("conditions", {}).get("status") != "consistent":
                        item["recommendation_group"], item["recommendation_label"] = (
                            "needs_verification",
                            "相关线索 · 待核对条件",
                        )
                    content = checksum(
                        {
                            key: item.get(key)
                            for key in [
                                "level",
                                "reasons",
                                "recommendation_group",
                                "requirements",
                                "deadline",
                                "opportunities",
                                "conditions",
                            ]
                        }
                    )
                    if existing:
                        recommendation = existing
                        recommendation.active = True
                        recommendation.result = {
                            **item,
                            "analysis": existing.result.get("analysis"),
                            "analysis_message": existing.result.get(
                                "analysis_message", analysis_message
                            ),
                            "input_signature": inputs,
                            "policy_signature": proof,
                        }
                        recommendation.save(update_fields=["active", "result", "updated_at"])
                        created = False
                    else:
                        recommendation, created = PolicyRecommendation.objects.get_or_create(
                            watch=watch,
                            policy=policy,
                            fingerprint=fingerprint,
                            defaults={
                                "content_fingerprint": content,
                                "profile_revision": watch.profile.revision,
                                "project_revision": watch.project.revision
                                if watch.project_id
                                else None,
                                "policy_version": policy.version,
                                "result": {
                                    **item,
                                    "analysis": analysis,
                                    "analysis_message": analysis_message,
                                    "input_signature": inputs,
                                    "policy_signature": proof,
                                },
                            },
                        )
                    watch.recommendations.filter(policy=policy, active=True).exclude(
                        pk=recommendation.pk
                    ).update(active=False)
                    if (
                        created
                        and run.notify_changes
                        and (not previous or previous.content_fingerprint != content)
                    ):
                        deliver_recommendation(recommendation)
                    run.matched += int(bool(relevant))
                else:
                    watch.recommendations.filter(policy=policy, active=True).update(active=False)
                run.scanned += 1
                run.cursor = policy.pk
                owner.update(
                    cursor=run.cursor,
                    scanned=run.scanned,
                    matched=run.matched,
                    message=f"已检查 {run.scanned} 份，相关 {run.matched} 份",
                    updated_at=timezone.now(),
                )
        with transaction.atomic():
            PolicyWatch.objects.select_for_update().get(pk=watch.pk)
            WatchRun.objects.select_for_update().get(pk=run.pk)
            guard()
            if len(policies) < config.batch_size:
                owner.update(
                    status="completed",
                    finished_at=timezone.now(),
                    lease_until=None,
                    lease_token=None,
                    message=f"检查完成：{run.scanned} 份，相关 {run.matched} 份",
                )
                PolicyWatch.objects.filter(pk=watch.pk).update(
                    input_signature=inputs,
                    has_baseline=True,
                    last_run_at=timezone.now(),
                    next_run_at=timezone.now() + timedelta(hours=watch.interval_hours),
                    event_watermark=run.cutoff,
                    message="已更新持续匹配结果。",
                    **({"last_full_scan_at": run.cutoff} if run.full_scan else {}),
                )
            else:
                owner.update(status="queued", lease_token=None, lease_until=None, failures=0)
    except WatchStopped as exc:
        if owner.update(
            status="cancelled",
            message=str(exc),
            finished_at=timezone.now(),
            lease_token=None,
            lease_until=None,
        ):
            PolicyWatch.objects.filter(pk=watch.pk, consent_version=watch.consent_version).update(
                next_run_at=timezone.now() + timedelta(minutes=config.aggregation_minutes),
                message=str(exc),
            )
    except Exception:
        failures = run.failures + 1
        updated = owner.update(
            status="failed" if failures >= 3 else "queued",
            failures=failures,
            retry_at=timezone.now() + timedelta(minutes=5 * 2 ** (failures - 1)),
            lease_token=None,
            lease_until=None,
            message="匹配暂未完成，已保留处理位置；连续失败三次后暂停，请在企业页面重新开启关注。",
        )
        if failures >= 3 and updated:
            PolicyWatch.objects.filter(pk=watch.pk).update(
                enabled=False, message="连续处理失败，已暂停持续关注；请核对后重新开启。"
            )


def prune_history():
    """Keep active recommendations and feedback; never delete policy/profile data."""
    platform = configuration()
    runs = cleared = 0
    for profile in EnterpriseProfile.objects.filter(policywatch__isnull=False).distinct().iterator():
        config = configuration(profile, base=platform)
        cutoff = timezone.now() - timedelta(days=config.retention_days)
        deleted, _ = WatchRun.objects.filter(watch__profile=profile,
            status__in=["completed", "failed", "cancelled"], created_at__lt=cutoff).delete()
        runs += deleted
        # Retain dedup fingerprints and notification links; discard old private AI text.
        cleared += PolicyRecommendation.objects.filter(watch__profile=profile, active=False, created_at__lt=cutoff, feedback="").exclude(result={}).update(result={})
    return {"runs_deleted": runs, "results_cleared": cleared}


def enterprise_usage(action, profile_id):
    return AuditRecord.objects.filter(action=action, created_at__date=timezone.localdate()).filter(
        Q(details__profile_id=str(profile_id)) | Q(object_id__in=WatchRun.objects.filter(watch__profile_id=profile_id).values("pk")))
