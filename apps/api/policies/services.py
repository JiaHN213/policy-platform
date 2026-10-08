import copy
import hashlib
from datetime import datetime, time

from core.errors import Conflict
from core.models import AuditRecord
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.exceptions import PermissionDenied, ValidationError

from .business_scope import configured_domains, configured_tags
from .extraction import extract_metadata
from .field_provenance import (
    latest_field_provenance,
    record_field_values,
    unlock_policy_field,
)
from .models import (
    Evidence,
    Opportunity,
    OpportunityBatch,
    Policy,
    PolicyEnrichment,
    PolicyRelation,
    PublicationEvent,
)
from .taxonomy import DocumentRole, OpportunityLevel, ValidityStatus


def opportunity_datetime(value):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        parsed_date = parse_date(value)
        parsed = datetime.combine(parsed_date, time.min) if parsed_date else None
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


@transaction.atomic
def publish_policy(
    policy_id,
    actor,
    expected_version,
    document_type=None,
    provenance=None,
    allow_incomplete_attachments=False,
):
    from subscriptions.models import Subscription

    if not actor.is_active or not actor.has_perm("policies.change_policy"):
        raise PermissionDenied("需要政策审核权限。")
    policy = Policy.objects.select_for_update().get(pk=policy_id)
    if policy.version != expected_version:
        raise Conflict()
    if policy.source_grade == "L4":
        raise ValidationError("L4 仅作线索。请另行收录并核实官方文件，不能直接升级此线索并发布。")
    if policy.status == Policy.Status.PUBLISHED:
        if provenance and any(getattr(policy, key) != value for key, value in provenance.items()):
            raise Conflict("已发布文件的地域与来源信息不能通过重复发布修改。")
        if document_type and document_type != policy.document_type:
            raise Conflict("已发布文件的类型不能通过重复发布修改。")
        return policy
    if policy.status != Policy.Status.CANDIDATE:
        raise Conflict("撤下的版本不能直接重新发布。")
    if not policy.body.strip():
        raise ValidationError("正文为空，无法发布。")
    incomplete_attachments = (
        policy.snapshots.exclude(parse_status="parsed").exists()
        or policy.discovereditem_set.filter(error_code="ATTACHMENTS_REQUIRE_REVIEW").exists()
    )
    if incomplete_attachments and not allow_incomplete_attachments:
        raise ValidationError("附件尚未完整解析，须处理后再正式发布。")
    selected_type = document_type or policy.document_type
    if selected_type not in Policy.DocumentType.values or selected_type == "unclassified":
        raise ValidationError("请先核实并选择文件类型。")
    policy.document_type = selected_type
    for key, value in (provenance or {}).items():
        if key not in {"source_grade", "geographic_level", "province", "city"}:
            raise ValidationError("不支持的来源信息字段。")
        setattr(policy, key, value)
    if policy.source_grade not in Policy.FORMAL_SOURCE_GRADES:
        raise ValidationError("正式发布必须核实为 L1、L2 或 L3 来源。")
    if policy.geographic_level not in {"national", "provincial", "city"}:
        raise ValidationError("请核实地域层级。")
    if policy.geographic_level == "national" and (policy.province or policy.city):
        raise ValidationError("国家级文件不应填写所属省市；适用地区另行记录。")
    if policy.geographic_level in {"provincial", "city"} and not policy.province.strip():
        raise ValidationError("省级、地级市级文件必须填写所属省级行政区。")
    if policy.geographic_level == "provincial" and policy.city:
        raise ValidationError("省级文件不应填写所属地级市。")
    if policy.geographic_level == "city" and not policy.city.strip():
        raise ValidationError("地级市级文件必须填写城市。")
    policy.status = Policy.Status.PUBLISHED
    if (
        policy.summary_method not in {"ai", "ai+manual"}
        or policy.extraction_version != policy.version
    ):
        metadata = extract_metadata(policy.body)
        policy.summary = metadata["summary"]
        policy.structured_keywords = metadata["structured_keywords"]
        policy.summary_method, policy.summary_evidence = "extractive", []
    policy.extraction_version = policy.version
    policy.published_at = timezone.now()
    if incomplete_attachments:
        policy.scope_evidence = {
            **(policy.scope_evidence or {}),
            "publication_override": {
                "reason": "incomplete_attachments",
                "actor_id": actor.pk,
                "at": timezone.now().isoformat(),
            },
        }
    policy.save(
        update_fields=[
            "summary",
            "summary_method",
            "summary_evidence",
            "structured_keywords",
            "extraction_version",
            "document_type",
            "source_grade",
            "geographic_level",
            "province",
            "city",
            "status",
            "published_at",
            "scope_evidence",
            "updated_at",
        ]
    )
    Evidence.objects.get_or_create(
        policy=policy,
        policy_version=policy.version,
        quote_hash=policy.content_hash,
        defaults={
            "text": policy.body,
            "location": {"kind": "body", "source_url": policy.source_url},
        },
    )
    if policy.document_type == "opportunity":
        job = (
            policy.enrichments.filter(policy_version=policy.version).order_by("-updated_at").first()
        )
        review = (job.result or {}).get("review", {}) if job else {}
        quote = next(
            (
                item.get("quote", "")
                for item in review.get("evidence", [])
                if len(item.get("quote", "").strip()) >= 5 and item.get("quote", "") in policy.body
            ),
            policy.body[: min(500, len(policy.body))],
        )
        opportunity, _ = Opportunity.objects.update_or_create(
            policy=policy,
            title=policy.title,
            defaults={
                "category": review.get("opportunity_category", "other"),
                "status": review.get("opportunity_status", "unverified"),
                "verification_status": "verified",
                "evidence_policy": policy,
                "evidence_version": policy.version,
                "evidence_quote": quote,
                "verified_at": timezone.now(),
            },
        )
        if review.get("opportunity_batch_name"):
            OpportunityBatch.objects.update_or_create(
                opportunity=opportunity,
                name=review["opportunity_batch_name"],
                defaults={
                    "status": review.get("opportunity_batch_status", "unverified"),
                    "starts_at": opportunity_datetime(review.get("opportunity_starts_at", "")),
                    "deadline_at": opportunity_datetime(review.get("opportunity_deadline_at", "")),
                    "verification_status": "verified",
                    "evidence_policy": policy,
                    "evidence_version": policy.version,
                    "evidence_quote": quote,
                    "verified_at": timezone.now(),
                },
            )
    PublicationEvent.objects.get_or_create(
        policy=policy,
        policy_version=policy.version,
        defaults={
            "payload": {
                "title": policy.title,
                "topics": policy.topics,
                "document_type": policy.document_type,
                "region": policy.region,
                "source_grade": policy.source_grade,
                "geographic_level": policy.geographic_level,
                "province": policy.province,
                "city": policy.city,
                "body": policy.body,
                "subscription_ids": [
                    str(i)
                    for i in Subscription.objects.filter(active=True).values_list("id", flat=True)
                ],
            }
        },
    )
    AuditRecord.objects.create(
        actor=actor,
        action="policy.publish",
        object_id=policy.id,
        details={
            "version": policy.version,
            "document_type": policy.document_type,
            "source_grade": policy.source_grade,
            "geographic_level": policy.geographic_level,
            "province": policy.province,
            "city": policy.city,
            "allow_incomplete_attachments": allow_incomplete_attachments,
        },
    )
    return policy


@transaction.atomic
def correct_policy(policy_id, actor, expected_version, changes):
    """Apply a narrow human correction on top of the current AI result."""
    from subscriptions.models import Subscription

    if not actor.is_active or not actor.has_perm("policies.change_policy"):
        raise PermissionDenied("需要政策审核权限。")
    policy = Policy.objects.select_for_update().get(pk=policy_id)
    if policy.version != expected_version:
        raise Conflict()

    old_version = policy.version
    previous_validity_status = policy.validity_status
    previous_job = (
        PolicyEnrichment.objects.select_for_update()
        .filter(policy=policy, policy_version=old_version, status="succeeded")
        .order_by("-updated_at")
        .first()
    )
    if previous_job is None:
        raise ValidationError("AI 审核尚未完成，请等待审核完成后再修正结果。")
    editable = {
        "summary",
        "document_type",
        "source_grade",
        "geographic_level",
        "province",
        "city",
        "validity_status",
        "validity_evidence",
        "business_domains",
        "direction_tags",
        "document_role",
        "opportunity_level",
        "support_signals",
    }
    changed = {
        key: value
        for key, value in changes.items()
        if key in editable and getattr(policy, key) != value
    }
    if not changed:
        return policy
    domains = configured_domains()
    tags = configured_tags()
    if "business_domains" in changed and any(
        key not in domains for key in changed["business_domains"]
    ):
        raise ValidationError("包含未知的水务业务领域。")
    if "direction_tags" in changed and any(key not in tags for key in changed["direction_tags"]):
        raise ValidationError("包含未知的技术与政策方向标签。")
    if "document_role" in changed and changed["document_role"] not in DocumentRole.values:
        raise ValidationError("包含未知的文档角色。")
    if (
        "opportunity_level" in changed
        and changed["opportunity_level"] not in OpportunityLevel.values
    ):
        raise ValidationError("包含未知的政策机会级别。")

    for key, value in changed.items():
        setattr(policy, key, value)
    if (
        policy.status == Policy.Status.PUBLISHED
        and policy.source_grade not in Policy.FORMAL_SOURCE_GRADES
    ):
        raise ValidationError("已发布政策的来源等级必须为 L1、L2 或 L3。")
    if policy.geographic_level == "national" and (policy.province or policy.city):
        raise ValidationError("国家级文件不应填写所属省市。")
    if policy.geographic_level in {"provincial", "city"} and not policy.province.strip():
        raise ValidationError("省级、地级市级文件必须填写所属省级行政区。")
    if policy.geographic_level == "provincial" and policy.city:
        raise ValidationError("省级文件不应填写所属地级市。")
    if policy.geographic_level == "city" and not policy.city.strip():
        raise ValidationError("地级市级文件必须填写城市。")
    if policy.validity_status != "unverified":
        if not policy.validity_evidence or policy.validity_evidence not in policy.body:
            raise ValidationError("政策效力修正必须附当前正文中的逐字证据。")
        if policy.document_type == "draft" and policy.validity_status not in {
            "consultation",
            "expired",
        }:
            raise ValidationError("征求意见稿不能标记为现行有效的正式政策。")

    policy.version += 1
    policy.extraction_version = policy.version
    if "summary" in changed:
        policy.summary_method = "ai+manual"
        policy.summary_evidence = []
    review = dict((policy.scope_evidence or {}).get("ai_review") or {})
    review["human_correction"] = {
        "actor_id": actor.pk,
        "fields": sorted(changed),
        "at": timezone.now().isoformat(),
    }
    policy.scope_evidence = {**(policy.scope_evidence or {}), "ai_review": review}
    policy.save()
    correction_evidence = {
        field_name: policy.validity_evidence
        for field_name in changed
        if field_name in {"validity_status", "validity_evidence"}
        and policy.validity_evidence
    }
    record_field_values(
        policy,
        changed.keys(),
        source_type="human_correction",
        locked=True,
        actor=actor,
        evidence=correction_evidence,
        evidence_location={
            field_name: {"kind": "body", "source_url": policy.source_url}
            for field_name in correction_evidence
        },
    )

    for evidence in list(policy.evidence.filter(policy_version=old_version)):
        evidence.pk = None
        evidence.policy_version = policy.version
        evidence.save(force_insert=True)
    PolicyRelation.objects.filter(evidence_policy=policy, evidence_version=old_version).update(
        evidence_version=policy.version
    )
    Opportunity.objects.filter(evidence_policy=policy, evidence_version=old_version).update(
        evidence_version=policy.version
    )
    OpportunityBatch.objects.filter(evidence_policy=policy, evidence_version=old_version).update(
        evidence_version=policy.version
    )

    result = copy.deepcopy(previous_job.result or {})
    result["human_correction"] = {
        "fields": sorted(changed),
        "actor_id": actor.pk,
    }
    previous_job.policy_version = policy.version
    previous_job.result = result
    previous_job.save(update_fields=["policy_version", "result", "updated_at"])

    if policy.status == Policy.Status.PUBLISHED:
        PublicationEvent.objects.get_or_create(
            policy=policy,
            policy_version=policy.version,
            kind="policy.corrected.v1",
            defaults={
                "payload": {
                    "title": policy.title,
                    "changed_fields": sorted(changed),
                    "previous_version": old_version,
                    "previous_validity_status": previous_validity_status,
                    "subscription_ids": [str(pk) for pk in Subscription.objects.filter(active=True, deleted_at__isnull=True).values_list("pk", flat=True)],
                    "change_details": [f"政策效力已调整为：{policy.get_validity_status_display()}"] if "validity_status" in changed else [],
                }
            },
        )
    AuditRecord.objects.create(
        actor=actor,
        action="policy.ai_review.corrected",
        object_id=policy.pk,
        details={
            "previous_version": old_version,
            "version": policy.version,
            "changed_fields": sorted(changed),
        },
    )
    return policy


@transaction.atomic
def unlock_policy_field_lock(policy_id, actor, expected_version, field_name):
    if not actor.is_active or not actor.has_perm("policies.change_policy"):
        raise PermissionDenied("需要政策审核权限。")
    policy = Policy.objects.select_for_update().get(pk=policy_id)
    if policy.version != expected_version:
        raise Conflict()
    latest = latest_field_provenance(policy).get(field_name)
    legacy = set(
        ((policy.scope_evidence or {}).get("obsidian_correction") or {}).get(
            "locked_fields", []
        )
    )
    if not (latest and latest.locked) and field_name not in legacy:
        return policy
    try:
        unlock_policy_field(policy, field_name, actor)
    except ValueError as exc:
        raise ValidationError("该字段不支持锁定或解锁。") from exc
    if field_name in legacy:
        scope = dict(policy.scope_evidence or {})
        correction = dict(scope.get("obsidian_correction") or {})
        correction["locked_fields"] = sorted(legacy - {field_name})
        scope["obsidian_correction"] = correction
        policy.scope_evidence = scope
        policy.save(update_fields=["scope_evidence", "updated_at"])
    AuditRecord.objects.create(
        actor=actor,
        action="policy.field.unlocked",
        object_id=policy.pk,
        details={"version": policy.version, "field_name": field_name},
    )
    return policy


@transaction.atomic
def withdraw_policy(policy_id, actor, expected_version):
    if not actor.is_active or not actor.has_perm("policies.change_policy"):
        raise PermissionDenied("需要政策审核权限。")
    policy = Policy.objects.select_for_update().get(pk=policy_id)
    if policy.version != expected_version:
        raise Conflict()
    policy.status = Policy.Status.WITHDRAWN
    policy.save(update_fields=["status", "updated_at"])
    PublicationEvent.objects.get_or_create(
        policy=policy, policy_version=policy.version, kind="policy.withdrawn.v1",
        defaults={"payload": {"title": policy.title}},
    )
    AuditRecord.objects.create(
        actor=actor,
        action="policy.withdraw",
        object_id=policy.id,
        details={"version": policy.version},
    )
    return policy


def fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@transaction.atomic
def update_validity(policy_id, actor, version, validity_status, validity_evidence):
    from subscriptions.models import Subscription

    if not actor.is_active or not actor.has_perm("policies.change_policy"):
        raise PermissionDenied("需要政策审核权限。")
    policy = Policy.objects.select_for_update().get(pk=policy_id)
    if policy.version != version:
        raise Conflict()
    if validity_status not in ValidityStatus.values:
        raise ValidationError("未知效力状态。")
    if validity_status != "unverified":
        if (
            policy.source_grade not in Policy.FORMAL_SOURCE_GRADES
            or len(validity_evidence.strip()) < 5
            or validity_evidence not in policy.body
        ):
            raise ValidationError("效力结论须以 L1–L3 当前原文中的逐字证据为依据。")
        if policy.document_type == "draft" and validity_status not in {"consultation", "expired"}:
            raise ValidationError("征求意见稿不能标记为现行有效的正式政策。")
    if policy.validity_status == validity_status and policy.validity_evidence == validity_evidence:
        return policy
    previous_validity_status = policy.validity_status
    policy.validity_status, policy.validity_evidence = validity_status, validity_evidence
    policy.version += 1
    metadata = extract_metadata(policy.body)
    policy.summary, policy.structured_keywords = (
        metadata["summary"],
        metadata["structured_keywords"],
    )
    policy.extraction_version = policy.version
    policy.summary_method, policy.summary_evidence = "extractive", []
    policy.save(
        update_fields=[
            "validity_status",
            "summary_method",
            "summary_evidence",
            "validity_evidence",
            "version",
            "summary",
            "structured_keywords",
            "extraction_version",
            "updated_at",
        ]
    )
    for evidence in list(policy.evidence.filter(policy_version=version)):
        evidence.pk = None
        evidence.policy_version = policy.version
        evidence.save(force_insert=True)
    # The body did not change; preserve current opportunity evidence references.
    Opportunity.objects.filter(evidence_policy=policy, evidence_version=version).update(evidence_version=policy.version)
    OpportunityBatch.objects.filter(evidence_policy=policy, evidence_version=version).update(evidence_version=policy.version)
    if policy.status == Policy.Status.PUBLISHED:
        PublicationEvent.objects.get_or_create(policy=policy, policy_version=policy.version, kind="policy.validity_changed.v1", defaults={"payload": {
            "title": f"政策效力变更为{policy.get_validity_status_display()}：{policy.title}"[:500],
            "previous_validity_status": previous_validity_status,
            "changed_fields": ["validity_status", "validity_evidence"],
            "subscription_ids": [str(pk) for pk in Subscription.objects.filter(active=True, deleted_at__isnull=True).values_list("pk", flat=True)],
        }})
    AuditRecord.objects.create(
        actor=actor,
        action="policy.validity",
        object_id=policy.pk,
        details={
            "version": policy.version,
            "validity_status": validity_status,
            "evidence_quote": validity_evidence,
        },
    )
    return policy


@transaction.atomic
def mark_as_lead(policy_id, actor, expected_version):
    if not actor.is_active or not actor.has_perm("policies.change_policy"):
        raise PermissionDenied("需要政策审核权限。")
    policy = Policy.objects.select_for_update().get(pk=policy_id)
    if policy.version != expected_version or policy.status != Policy.Status.CANDIDATE:
        raise Conflict("仅可将当前待审核版本标为线索。")
    if policy.source_grade != "L4":
        policy.source_grade = "L4"
        policy.version += 1
        policy.save(update_fields=["source_grade", "version", "updated_at"])
        AuditRecord.objects.create(
            actor=actor,
            action="policy.mark_lead",
            object_id=policy.id,
            details={"version": policy.version, "source_grade": "L4"},
        )
    return policy
