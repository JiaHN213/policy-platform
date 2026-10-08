"""P4: finite, evidence-preserving recovery of single-policy review failures."""
import re
import time
from datetime import datetime, timedelta
from datetime import time as day_time
from pathlib import PurePosixPath
from urllib.parse import urlparse

from core.ai_runtime import get_ai_profile
from core.business_config import checksum, config_version
from core.models import AuditRecord
from core.storage import read_original
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from ingestion.diagnostics import attachment_failure
from ingestion.documents import extract_attachment_text

from .field_provenance import locked_policy_fields
from .models import AIReviewControl, Policy, PolicyEnrichment, PublicationEvent, ReviewRecovery
from .readiness import evidence_readiness
from .recovery_context import RecoveryInterrupted, current
from .services import fingerprint


def classify(job):
    policy = job.policy
    readiness = evidence_readiness(policy)
    if not policy.body.strip() and not policy.snapshots.exclude(parse_status="parsed").exists():
        return "original", "正文为空且没有可重解析的原件，请补充官方原文后重新处理。"
    if readiness["status"] == "incomplete":
        return "parse", "先重解析已保存但未解析的附件；未下载、扫描件或不支持的格式需补充原件或人工处理。"
    if job.status == "succeeded":
        return "human", "AI 已完成审核，但内容或发布条件仍需判断；请核对分类依据和缺失字段，避免无变化重复审核。"
    if job.error_code == "SOURCE_CHANGED":
        return "human", "来源或人工修改已改变文件，请处理当前版本，不重放旧审核。"
    if job.error_code in {"INVALID_SUMMARY_QUOTE", "INVALID_KEYWORD"}:
        return "quote", "从当前正文重新提取连续原文要点，再调用原审核流程；不模糊替换数字或拼接引文。"
    if job.error_code in {"CONTEXT_TOO_LARGE", "BODY_EMPTY_OR_TOO_LARGE"}:
        return "segment", "重新分段处理长文；超长正文按相关内容取样并保留覆盖提示，不声称已读完整全文。"
    if job.error_code == "MODEL_INVALID_OUTPUT":
        return "structure", "使用原结构约束重新生成精简输出，已成功的分段结果可复用。"
    return "retry", "恢复超时、连接或处理中断任务，复用已保存分段结果；持续失败将暂停，避免循环重试。"


def enqueue(job, user=None, automatic=False):
    existing = ReviewRecovery.objects.filter(pk=job.recovery_token).first() if job.recovery_token else None
    if existing and existing.status in {"queued", "running", "blocked", "succeeded"}:
        return existing
    if job.policy_version != job.policy.version or job.policy.status not in {"candidate", "published"} or job.policy.source_grade == "L4":
        raise ValueError("请对当前版本的正式来源文件进行异常处理。")
    if job.status == "running" and job.lease_until and job.lease_until > timezone.now():
        raise ValueError("审核任务仍在运行，请等待完成或超时后再处理。")
    if job.status not in {"failed", "running", "succeeded"}:
        raise ValueError("该文件尚未发生审核异常，无需恢复。")
    if job.status == "succeeded" and (job.policy.status == "published" or (job.result.get("review") or {}).get("decision") == "exclude"):
        raise ValueError("该文件已发布或已排除，无需异常恢复。")
    category, reason = classify(job)
    defaults = {
        "policy": job.policy, "policy_version": job.policy.version, "category": category,
        "message": reason, "automatic": automatic, "requested_by": user,
        "result": {"before": {"status": job.status, "error": job.error_code,
                                "version": job.policy.version}, "steps": []}}
    record = existing or ReviewRecovery.objects.get_or_create(source_job=job, defaults=defaults)[0]
    control, _ = AIReviewControl.objects.get_or_create(singleton_key="default")
    if record.status in {"failed", "cancelled"} and record.attempts < control.recovery_attempt_limit:
        if record.retry_at and record.retry_at > timezone.now():
            raise ValueError("该文件尚在冷却时间内，请稍后再试。")
        ReviewRecovery.objects.filter(pk=record.pk, status=record.status).update(
            status="queued", stage="等待继续处理", message=reason, requested_by=user, automatic=automatic)
        record.refresh_from_db()
    return record


def stop(record):
    ReviewRecovery.objects.filter(pk=record.pk, status__in=["queued", "running"]).update(
        status="cancelled", stage="已停止", message="已停止异常处理，不再应用迟到的 AI 结果；已保存的原文解析不撤销。", lease_until=None)


def source_signature(policy):
    profile = get_ai_profile("review")
    return checksum({"id": str(policy.pk), "version": policy.version, "body": policy.body,
                     "title": policy.title, "number": policy.document_number,
                     "date": policy.publication_date.isoformat(), "grade": policy.source_grade,
                     "status": policy.status, "locks": sorted(locked_policy_fields(policy)),
                     "config": config_version(), "model": profile.model,
                     "endpoint": profile.base_url, "enabled": profile.enabled})


class RecoveryContext:
    def __init__(self, record, guard):
        self.record, self.guard = record, guard
        self.signature = source_signature(record.policy)
        self.started, self.calls, self.characters = time.monotonic(), 0, 0
        self.quote_repair = record.category == "quote"
        self.interruption = ""

    def cached(self, instruction, data, schema):
        key = checksum({"signature": self.signature, "instruction": instruction,
                        "data": data, "schema": schema.model_json_schema()})
        saved = ReviewRecovery.objects.get(pk=self.record.pk).result.get("checkpoints", {}).get(key)
        return key, schema.model_validate(saved) if saved is not None else None

    def before_request(self, characters):
        self.guard("正在恢复分段摘要、结构化输出与原文校验")
        if self.calls >= 20 or self.characters + characters > 180000 or time.monotonic() - self.started > 900:
            raise RecoveryInterrupted("已达到本次模型调用或时间预算，已保留检查点，请人工查看或冷却后继续。")
        self.calls += 1
        self.characters += characters
        record = ReviewRecovery.objects.get(pk=self.record.pk)
        result = {**record.result, "model_calls": record.result.get("model_calls", 0) + 1,
                  "input_characters": record.result.get("input_characters", 0) + characters}
        ReviewRecovery.objects.filter(pk=record.pk, status="running", attempts=self.record.attempts).update(result=result)

    def save_checkpoint(self, key, output):
        self.guard("正在保存已完成的分段检查点")
        record = ReviewRecovery.objects.get(pk=self.record.pk)
        points = {**record.result.get("checkpoints", {}), key: output.model_dump(mode="json")}
        ReviewRecovery.objects.filter(pk=record.pk, status="running", attempts=self.record.attempts).update(
            result={**record.result, "checkpoints": points})


def reparse(record, guard):
    """Only supported stored originals. No network fallback or parser installation."""
    policy = Policy.objects.get(pk=record.policy_id)
    if "body" in locked_policy_fields(policy):
        return None, ["正文已被人工锁定，请人工确认后再补入附件内容。"]
    snapshots = list(policy.snapshots.exclude(parse_status="parsed").order_by("id")[:3])
    parsed, issues = [], []
    for snapshot in snapshots:
        guard("正在重解析已保存的官方附件")
        if snapshot.size_bytes > 10 * 1024 * 1024:
            issues.append("附件超过单次 10MB 处理上限，请人工单独解析。")
            continue
        try:
            content = read_original(snapshot.object_key)
            if len(content) > 10 * 1024 * 1024:
                raise ValueError("too large")
            text = extract_attachment_text(content, PurePosixPath(urlparse(snapshot.url).path).suffix.lstrip("."))
            if len(text.strip()) < 10 or len(text) > 150000:
                issues.append("附件无足够可读文字或解析文字超过处理上限，请核对原件。")
                continue
            parsed.append((snapshot, text))
        except Exception as exc:
            reason = attachment_failure(exc, "parse")
            if reason == "ATTACHMENT_PARSER_REQUIRED":
                reason = "当前未安装适用于此附件的解析器；请提供可复制文字的 PDF、Word 或表格原件，或人工补充内容。"
            elif re.fullmatch(r"[A-Z0-9_]+", reason):
                reason = "附件未能提取可读正文，可能是扫描件、加密或损坏文件；请核对原件，当前不会自动安装解析器或进行 OCR。"
            issues.append(reason)
    if not parsed:
        return None, issues or ["未找到可读取的已保存附件，请先下载原件或补充正文；不会凭空生成缺失材料。"]
    with transaction.atomic():
        locked = Policy.objects.select_for_update().get(pk=policy.pk)
        ReviewRecovery.objects.select_for_update().get(pk=record.pk)
        guard("正在核对版本并保存附件解析")
        if locked.version != policy.version or locked.body != policy.body:
            raise RecoveryInterrupted("原文已变化，未补入旧版本附件解析，请重新处理当前版本。")
        if "body" in locked_policy_fields(locked):
            raise RecoveryInterrupted("正文已被人工锁定，未自动补入附件解析内容。")
        body = locked.body
        for snapshot, text in parsed:
            fresh = locked.snapshots.select_for_update().get(pk=snapshot.pk)
            if fresh.sha256 != snapshot.sha256 or fresh.object_key != snapshot.object_key:
                raise RecoveryInterrupted("附件原件已经变化，请重新处理。")
            if text not in body:
                name = PurePosixPath(urlparse(snapshot.url).path).name or "官方附件"
                body += f"\n\n附件：{name}\n{text}"
            fresh.parse_status = "parsed"
            fresh.save(update_fields=["parse_status", "updated_at"])
        # Always version the evidence change, including an already-present attachment text.
        locked.body, locked.version = body, locked.version + 1
        locked.content_hash, locked.extraction_version = fingerprint(body), 0
        locked.save(update_fields=["body", "version", "content_hash", "extraction_version", "updated_at"])
        target, _ = PolicyEnrichment.objects.get_or_create(policy=locked, policy_version=locked.version,
            defaults={"recovery_token": record.pk})
        ReviewRecovery.objects.filter(pk=record.pk).update(policy_version=locked.version)
        AuditRecord.objects.create(action="policy.recovery.attachments_parsed", object_id=locked.pk,
            details={"recovery_id": str(record.pk), "version": locked.version,
                     "snapshots": [str(s.pk) for s, _ in parsed]})
        if locked.status == "published":
            PublicationEvent.objects.get_or_create(policy=locked, policy_version=locked.version,
                kind="policy.updated.v1", defaults={"payload": {"reason": "附件重新解析", "previous_status": "published"}})
        fresh_record = ReviewRecovery.objects.get(pk=record.pk)
        fresh_record.result = {**fresh_record.result, "target_job": str(target.pk),
                               "parsed_attachments": [str(s.pk) for s, _ in parsed], "parse_notes": issues}
        fresh_record.save(update_fields=["result", "updated_at"])
    return target, issues


def process(record_id):
    record = ReviewRecovery.objects.get(pk=record_id)
    with transaction.atomic():
        source = PolicyEnrichment.objects.select_for_update().get(pk=record.source_job_id)
        record = ReviewRecovery.objects.select_for_update().get(pk=record_id)
        control, _ = AIReviewControl.objects.select_for_update().get_or_create(singleton_key="default")
        now = timezone.now()
        if record.status not in {"queued", "running"} or (record.lease_until and record.lease_until > now) or (record.retry_at and record.retry_at > now):
            return
        if record.automatic and (not control.recovery_enabled or not control.enabled):
            return
        if record.attempts >= control.recovery_attempt_limit:
            record.status, record.stage, record.message = "blocked", "需要人工处理", "已达到本文件的恢复次数上限，请人工核对，不再自动重复尝试。"
            record.lease_until = None
            record.save()
            return
        used = AuditRecord.objects.filter(action="policy.recovery.attempt_started", created_at__date=timezone.localdate()).count()
        if used >= control.recovery_daily_limit:
            tomorrow = timezone.localdate() + timedelta(days=1)
            record.retry_at = timezone.make_aware(datetime.combine(tomorrow, day_time()))
            record.stage, record.message = "等待次日额度", "已达到今天的异常处理上限，将在明天继续。"
            record.save()
            return
        record.attempts += 1
        record.status, record.lease_until = "running", now + timedelta(minutes=28)
        record.save()
        source.recovery_token = record.pk
        source.save(update_fields=["recovery_token", "updated_at"])
        AuditRecord.objects.create(action="policy.recovery.attempt_started", object_id=record.pk,
                                   details={"attempt": record.attempts})
    attempt = record.attempts
    signature = None
    target = source
    interrupt_message = ""

    def guard(stage):
        state = ReviewRecovery.objects.get(pk=record_id)
        if state.status != "running" or state.attempts != attempt:
            raise RecoveryInterrupted("任务已经停止或已被后续处理替代。")
        if state.requested_by_id:
            user = get_user_model().objects.filter(pk=state.requested_by_id, is_active=True, is_staff=True).first()
            if not user or not user.has_perm("policies.change_policy"):
                raise RecoveryInterrupted("发起人已无政策管理权限，本次结果未应用。")
        active = AIReviewControl.objects.get(singleton_key="default")
        if state.automatic and (not active.recovery_enabled or not active.enabled):
            raise RecoveryInterrupted("自动异常处理或自动审核已暂停，本次结果未应用。")
        policy = Policy.objects.get(pk=state.policy_id)
        if policy.version != state.policy_version or policy.status not in {"candidate", "published"} or policy.source_grade == "L4":
            raise RecoveryInterrupted("政策版本或发布状态已改变，请按当前文件重新处理。")
        if signature and source_signature(policy) != signature:
            raise RecoveryInterrupted("原文、人工锁定或审核配置已变化，未覆盖新的修改。")
        ReviewRecovery.objects.filter(pk=record_id, status="running", attempts=attempt).update(stage=stage)

    def finish(policy, output):
        fresh = ReviewRecovery.objects.select_for_update().get(pk=record_id)
        if fresh.status != "running" or fresh.attempts != attempt:
            raise RecoveryInterrupted("任务已停止，未应用审核结果。")
        decision = output.get("review", {}).get("decision")
        fresh.status = "succeeded" if policy.status == "published" or decision == "exclude" else "blocked"
        fresh.stage = "处理完成" if fresh.status == "succeeded" else "仍需人工处理"
        fresh.message = "已重新审核并发布。" if policy.status == "published" else "已重新审核并排除。" if decision == "exclude" else "恢复审核已完成，但分类或发布条件仍不足，请查看审核依据并人工核对。"
        fresh.result = {**fresh.result, "after": {"policy_status": policy.status, "decision": decision,
                                                  "version": policy.version, "finalization": output.get("finalization")}}
        fresh.lease_until = None
        fresh.save()

    try:
        guard("正在分析异常原因")
        if record.category in {"original", "human"}:
            raise RecoveryInterrupted(record.message)
        target_id = record.result.get("target_job")
        if target_id:
            target = PolicyEnrichment.objects.get(pk=target_id)
        elif record.category == "parse":
            target, notes = reparse(record, guard)
            if not target:
                raise RecoveryInterrupted("；".join(notes))
        record.refresh_from_db()
        record.policy.refresh_from_db()
        signature = source_signature(record.policy)
        guard("准备恢复原审核流程")
        with transaction.atomic():
            target = PolicyEnrichment.objects.select_for_update().get(pk=target.pk)
            guard("准备恢复原审核流程")
            if target.status == "running" and target.lease_until and target.lease_until > timezone.now() and target.recovery_token != record.pk:
                raise RecoveryInterrupted("该文件已有审核正在运行，未启动重复任务。")
            target.status, target.retry_at, target.lease_until, target.recovery_token = "queued", None, None, record.pk
            target.result = {k: v for k, v in target.result.items() if k != "reuse_validated_ai_result"}
            target.save()
        context = RecoveryContext(record, guard)
        token = current.set(context)
        try:
            from .enrichment import process_one
            process_one(target.pk, recovery_token=record.pk, guard=guard, finish=finish)
        finally:
            current.reset(token)
        target.refresh_from_db()
        record.refresh_from_db()
        if record.status == "running":
            reasons = {
                "MODEL_TIMEOUT": "模型响应超时，请检查模型负载；已完成的分段结果可在冷却后继续。",
                "MODEL_INVALID_OUTPUT": "模型仍未返回完整的结构化结果，请检查模型能力或更换模型后继续。",
                "CONTEXT_TOO_LARGE": "正文和审核输入仍超过模型处理上限，请拆分材料或缩减单份附件后继续。",
                "INVALID_SUMMARY_QUOTE": "摘要引用无法在原文中找到，下次将直接提取连续原文要点，再重新审核。",
                "INVALID_KEYWORD": "关键词无法在原文中找到，下次将重新提取原文要点。",
                "INVALID_REVIEW_QUOTE": "审核依据无法在原文中找到；未采用该结果，请核对正文或重新生成。",
                "INVALID_REVIEW_VALUE": "审核中的数字、日期或字段值缺少有效原文依据；未采用该结果。",
                "SOURCE_CHANGED": "政策原文或版本已改变，请处理当前版本。",
            }
            if target.error_code in {"INVALID_SUMMARY_QUOTE", "INVALID_KEYWORD", "INVALID_REVIEW_QUOTE", "INVALID_REVIEW_VALUE"}:
                # Schema-valid output may still fail grounding: never replay it forever.
                record.result = {**record.result, "checkpoints": {}}
            if target.error_code in {"INVALID_SUMMARY_QUOTE", "INVALID_KEYWORD"}:
                record.category = "quote"
            ReviewRecovery.objects.filter(pk=record_id, status="running", attempts=attempt).update(
                category=record.category, result=record.result)
            raise RecoveryInterrupted(context.interruption or reasons.get(target.error_code,
                "重新审核未能完成，请检查模型连接和服务日志；已完成的分段结果可在冷却后继续。"))
    except RecoveryInterrupted as exc:
        interrupt_message = str(exc)
    except Exception:
        interrupt_message = "恢复过程遇到连接、读取或保存异常，原有人工结果保留；可在冷却后继续处理。"
    finally:
        if interrupt_message:
            terminal = record.category in {"original", "human"} or (record.category == "parse" and not target) or record.attempts >= control.recovery_attempt_limit
            ReviewRecovery.objects.filter(pk=record_id, status="running", attempts=attempt).update(
                status="blocked" if terminal else "failed", stage="需要人工处理" if terminal else "恢复未完成",
                message=interrupt_message, lease_until=None,
                retry_at=timezone.now() + timedelta(minutes=control.recovery_cooldown_minutes))
        # Fence cleanup too: an expired worker must not cancel a newer attempt.
        # Use the same job-before-recovery lock order as claiming/finalizing work.
        with transaction.atomic():
            PolicyEnrichment.objects.select_for_update().get(pk=record.source_job_id)
            if target and target.pk != record.source_job_id:
                PolicyEnrichment.objects.select_for_update().get(pk=target.pk)
            state = ReviewRecovery.objects.select_for_update().get(pk=record_id)
            if state.attempts == attempt:
                PolicyEnrichment.objects.filter(policy_id=record.policy_id, recovery_token=record.pk, status="running").update(
                    status="failed", lease_until=None)


def automatic_candidates():
    return PolicyEnrichment.objects.filter(policy_version=F("policy__version"), policy__status="candidate",
        policy__is_demo=False, recovery__isnull=True, recovery_token__isnull=True).exclude(policy__source_grade="L4").filter(
        Q(status="failed", attempts__gte=3) | Q(status="running", lease_until__lte=timezone.now()) |
        Q(status="succeeded", result__review__decision="include")).order_by("updated_at")
