"""Owner-reviewed matching samples; no model-generated reference answers."""

import random
from datetime import timedelta

from accounts.models import Membership, User
from accounts.services import access_decision
from core.business_config import checksum, config_version
from core.errors import Conflict
from core.models import AuditRecord
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from enterprises.matching import match_policies
from enterprises.models import EnterpriseProfile, EnterpriseProject, PolicyWatch, WatchRun
from enterprises.policy_evidence import policy_signature
from policies.catalog import formal_policies
from policies.models import Policy
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import MatchingCase, MatchingObservation, MatchingStudy


def accessible_studies(user):
    if not user.is_active or not access_decision(user, "policy_detail")["allowed"]:
        return MatchingStudy.objects.none()
    return MatchingStudy.objects.filter(
        user=user,
        profile__organization__membership__user=user,
        profile__organization__membership__active=True,
    ).distinct()


def profile_snapshot(profile, project):
    return {
        "name": profile.organization.name,
        "profile": profile.data,
        "revision": profile.revision,
        "project": {
            "name": project.name,
            "data": project.data,
            "description": project.description,
            "revision": project.revision,
        }
        if project
        else None,
        "config": config_version(),
    }


def create_study(user, profile, project=None, view="opportunities", limit=20):
    if view not in {"policies", "opportunities"} or not 4 <= limit <= 40:
        raise ValidationError("请选择政策或机会视角，样本数为 4 至 40。")
    if (
        not profile.confirmed_at
        or not Membership.objects.filter(
            user=user, organization_id=profile.organization_id, active=True
        ).exists()
        or not access_decision(user, "policy_detail")["allowed"]
    ):
        raise PermissionDenied("请先确认有权访问的企业画像。")
    if project and project.profile_id != profile.pk:
        raise ValidationError("项目不属于当前企业。")
    if (
        MatchingStudy.objects.filter(
            user=user, created_at__gte=timezone.now() - timedelta(days=1)
        ).count()
        >= 5
    ):
        raise ValidationError("每天最多创建 5 组核验样本，请先完成已有标注。")
    snapshot = profile_snapshot(profile, project)
    signature = checksum(snapshot)
    pool = list(formal_policies(user).exclude(is_demo=True).values_list("pk", flat=True))
    if not pool:
        raise ValidationError("暂无正式政策可用于核验。")
    # Use the same database path as P5, without invoking models or search engines.
    initial = match_policies(user, profile, project, view, policy_ids=pool)
    positive = {
        item["policy_id"] for item in initial["items"] if item["level"] in {"high", "medium"}
    }
    buckets = [
        [pk for pk in pool if str(pk) in positive],
        [pk for pk in pool if str(pk) not in positive],
    ]
    rng = random.SystemRandom()
    for bucket in buckets:
        rng.shuffle(bucket)
    selected = buckets[0][: limit // 2] + buckets[1][: limit // 2]
    selected += [pk for pk in buckets[0] + buckets[1] if pk not in selected][
        : max(0, limit - len(selected))
    ]
    with transaction.atomic():
        User.objects.select_for_update(no_key=True).get(pk=user.pk)
        if (
            MatchingStudy.objects.filter(
                user=user, created_at__gte=timezone.now() - timedelta(days=1)
            ).count()
            >= 5
        ):
            raise ValidationError("每天最多创建 5 组核验样本，请先完成已有标注。")
        current_profile = EnterpriseProfile.objects.select_for_update().get(pk=profile.pk)
        current_project = (
            EnterpriseProject.objects.select_for_update().get(pk=project.pk) if project else None
        )
        member = (
            Membership.objects.select_for_update()
            .filter(user=user, organization_id=profile.organization_id, active=True)
            .first()
        )
        if (
            not member
            or not current_profile.confirmed_at
            or not access_decision(user, "policy_detail")["allowed"]
        ):
            raise PermissionDenied("当前企业访问权限已变化，请刷新页面。")
        if checksum(profile_snapshot(current_profile, current_project)) != signature:
            raise Conflict("企业资料或配置已变化，请重新抽样。")
        nodes = list(Policy.objects.select_for_update().filter(pk__in=selected).order_by("pk"))
        if len(nodes) != len(selected) or formal_policies(user).filter(
            pk__in=selected
        ).count() != len(selected):
            raise Conflict("政策可见范围已变化，请重新抽样。")
        proofs = {p.pk: policy_signature(user, p) for p in nodes}
        items = {
            i["policy_id"]: i
            for i in match_policies(
                user, current_profile, current_project, view, policy_ids=selected
            )["items"]
        }
        study = MatchingStudy.objects.create(
            user=user,
            profile=profile,
            project=project,
            view=view,
            snapshot=snapshot,
            selection={
                "population": len(pool),
                "sample_size": len(nodes),
                "predicted_positive_pool": len(buckets[0]),
                "predicted_negative_pool": len(buckets[1]),
                "method": "按推荐与未推荐分层抽样；比例不代表全库分布",
                "prediction_mode": "当前规则匹配；未调用模型",
            },
        )
        for policy in nodes:
            if policy_signature(user, policy) != proofs[policy.pk]:
                raise Conflict("政策机会或窗口在抽样中变化，请重试。")
            prediction = items.get(
                str(policy.pk),
                {
                    "level": "low",
                    "reasons": [
                        "原匹配流程未将此文件列为当前相关结果，可能因效力、窗口或明确条件冲突被排除。"
                    ],
                },
            )
            MatchingCase.objects.create(
                study=study,
                policy=policy,
                predicted=prediction["level"] in {"high", "medium"},
                prediction=prediction,
                snapshot={
                    "title": policy.title,
                    "body": policy.body,
                    "version": policy.version,
                    "source_url": policy.source_url,
                    "source_grade": policy.source_grade,
                    "publication_date": policy.publication_date.isoformat(),
                    "policy_signature": proofs[policy.pk],
                    "attachments": list(policy.snapshots.values("url", "parse_status")),
                },
            )
        AuditRecord.objects.create(
            actor=user,
            action="matching.study.created",
            object_id=study.pk,
            details={"samples": len(nodes), "view": view},
        )
    return study


def metrics(study):
    counts = {
        "tp": 0,
        "fp": 0,
        "fn": 0,
        "tn": 0,
        "pending": 0,
        "unsure": 0,
        "evidence_yes": 0,
        "evidence_no": 0,
    }
    for item in study.cases.all():
        if item.verdict in {"pending", "unsure"}:
            counts[item.verdict] += 1
            continue
        key = (
            "tp"
            if item.predicted and item.verdict == "relevant"
            else "fp"
            if item.predicted
            else "fn"
            if item.verdict == "relevant"
            else "tn"
        )
        counts[key] += 1
        if item.evidence_supported is not None:
            counts["evidence_yes" if item.evidence_supported else "evidence_no"] += 1
    judged = sum(counts[k] for k in ["tp", "fp", "fn", "tn"])
    total = judged + counts["pending"] + counts["unsure"]

    def ratio(n, d):
        return round(n / d, 4) if d else None

    return {
        **counts,
        "total": total,
        "judged": judged,
        "precision": ratio(counts["tp"], counts["tp"] + counts["fp"]),
        "recall": ratio(counts["tp"], counts["tp"] + counts["fn"]),
        "coverage": ratio(judged, total),
        "evidence_support": ratio(
            counts["evidence_yes"], counts["evidence_yes"] + counts["evidence_no"]
        ),
        "notice": "仅为本组已明确标注样本内的指标；分层抽样、未标注和无法判断会影响代表性，不能当作全库准确率或真实业务资格认证。",
    }


def label_case(user, case_id, data):
    with transaction.atomic():
        User.objects.select_for_update(no_key=True).get(pk=user.pk)
        item = get_object_or_404(
            MatchingCase.objects.select_for_update(), pk=case_id, study__in=accessible_studies(user)
        )
        if (
            not Membership.objects.select_for_update()
            .filter(user=user, organization_id=item.study.profile.organization_id, active=True)
            .first()
        ):
            raise PermissionDenied("当前企业访问权限已变化。")
        if data["label_version"] != item.label_version:
            raise Conflict("这条标注已修改，请刷新后再保存。")
        quote = data.get("quote", "").strip()
        if quote and quote not in item.snapshot["body"]:
            raise ValidationError("依据必须是本次保存的政策原文中的连续文字。")
        if data["verdict"] == "relevant" and len(quote) < 6:
            raise ValidationError("请为相关判断引用至少 6 个字符的原文依据。")
        before = {
            key: getattr(item, key)
            for key in ["verdict", "evidence_supported", "notes", "quote", "label_version"]
        }
        item.verdict = data["verdict"]
        item.evidence_supported = data.get("evidence_supported")
        item.notes = data.get("notes", "")
        item.quote = quote
        item.label_version += 1
        item.labeled_at = timezone.now()
        item.save()
        AuditRecord.objects.create(
            actor=user,
            action="matching.case.labeled",
            object_id=item.pk,
            details={"before": before, "version": item.label_version},
        )
    return item


def observe_runtime():
    now = timezone.now()
    runs = WatchRun.objects.filter(created_at__gte=now - timedelta(days=7))
    completed = runs.filter(status="completed", finished_at__isnull=False)
    durations = sorted(
        (end - start).total_seconds()
        for start, end in completed.values_list("created_at", "finished_at")
        if end >= start
    )
    active = PolicyWatch.objects.filter(enabled=True).count()
    oldest = (
        WatchRun.objects.filter(status="queued")
        .order_by("created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    return {
        "observed_at": now.isoformat(),
        "enabled_watches": active,
        "runs_7d": runs.count(),
        "states": list(runs.values("status").annotate(count=Count("id"))),
        "completed_7d": len(durations),
        "p95_run_seconds": durations[max(0, (len(durations) * 95 + 99) // 100 - 1)]
        if durations
        else None,
        "oldest_queued_at": oldest.isoformat() if oldest else None,
        "expired_leases": WatchRun.objects.filter(status="running", lease_until__lt=now).count(),
        "over_24h": WatchRun.objects.filter(
            status__in=["queued", "running"], created_at__lt=now - timedelta(hours=24)
        ).count(),
        "pending_labels": MatchingCase.objects.filter(verdict="pending").count(),
        "status": "no_data" if not active and not runs.exists() else "observing",
        "notice": "耗时为排队至本轮完成，包含预算等待，不是发现新政策至通知送达时间；无运行样本时不评价达标。",
    }


def record_observation():
    metrics = observe_runtime()
    hour = timezone.now().replace(minute=0, second=0, microsecond=0)
    record, _ = MatchingObservation.objects.update_or_create(
        hour=hour, defaults={"metrics": metrics}
    )
    MatchingObservation.objects.filter(hour__lt=hour - timedelta(days=90)).delete()
    return str(record.pk)
