"""Export a Chinese Obsidian vault and safely import explicit policy edits."""

import errno
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import yaml
from core.models import AuditRecord
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from policies.business_scope import configured_domains, configured_tags
from policies.field_provenance import record_field_values
from policies.models import Evidence, Policy, PolicyEnrichment
from policies.taxonomy import ValidityStatus

from .models import KnowledgePage

MANIFEST = ".policy-wiki-export.json"
SUMMARY_START = "<!-- POLICY_SUMMARY_START -->"
SUMMARY_END = "<!-- POLICY_SUMMARY_END -->"
BODY_START = "<!-- POLICY_BODY_START -->"
BODY_END = "<!-- POLICY_BODY_END -->"
FOLDERS = {"chain": "02-政策链", "topic": "03-专题研究", "region": "04-地区专题"}
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sha256(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _safe_name(value, limit=90):
    value = INVALID_FILENAME.sub(" ", value).strip(" .")
    value = re.sub(r"\s+", " ", value)
    # Linux NAME_MAX counts UTF-8 bytes, not Chinese characters. Leave room
    # for the stable ID, extension and atomic-write temporary suffix.
    value = value[:limit].encode("utf-8")[:180].decode("utf-8", errors="ignore")
    return (value.rstrip(" .") or "未命名")


def _existing_file(path):
    try:
        return path.is_file()
    except OSError as exc:
        # Older Windows exports may reference names Linux cannot even stat.
        # Treat only that case as absent; permission/disk failures must surface.
        if exc.errno == errno.ENAMETOOLONG:
            return False
        raise


def _policy_for_page(page):
    source = next(iter(page.page_sources.all()), None)
    return source.policy if source else None


def _page_path(page):
    if page.page_type != "policy":
        return Path(FOLDERS[page.page_type]) / f"{_safe_name(page.title)}〔{str(page.pk)[:8]}〕.md"
    policy = _policy_for_page(page)
    if policy is None:
        return Path("01-政策文件") / "待核实" / f"{_safe_name(page.title)}〔{str(page.pk)[:8]}〕.md"
    levels = {
        "national": "国家级",
        "provincial": "省级",
        "city": "地级市级",
        "unverified": "待核实",
    }
    document_types = dict(Policy.DocumentType.choices)
    return (
        Path("01-政策文件")
        / levels.get(policy.geographic_level, "待核实")
        / document_types.get(policy.document_type, "其他文件")
        / f"{_safe_name(policy.title)}〔{str(policy.pk)[:8]}〕.md"
    )


def _frontmatter(page, policy=None):
    revision = page.current_revision
    values = {
        "title": page.title,
        "page_type": page.page_type,
        "revision": revision.number,
        "source_count": page.source_count,
        "built_at": page.built_at.isoformat() if page.built_at else "",
        "generated": True,
        "read_only": policy is None,
    }
    if policy is not None:
        domains = configured_domains()
        tags = configured_tags()
        values.update(
            {
                "policy_id": str(policy.pk),
                "policy_version": policy.version,
                "可编辑": True,
                "政策名称": policy.title,
                "发文机关": policy.issuer,
                "发文字号": policy.document_number,
                "发布日期": policy.publication_date.isoformat(),
                "政策效力": policy.get_validity_status_display(),
                "政策效力依据": policy.validity_evidence,
                "文件类型": policy.get_document_type_display(),
                "业务领域": [domains[key][0] for key in policy.business_domains if key in domains],
                "方向标签": [tags[key][0] for key in policy.direction_tags if key in tags],
            }
        )
    lines = ["---"]
    lines += [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in values.items()]
    lines += ["---", ""]
    return "\n".join(lines)


def _editable_policy_sections(policy):
    return (
        "## 政策摘要（可编辑）\n\n"
        f"{SUMMARY_START}\n{policy.summary.strip()}\n{SUMMARY_END}\n\n"
        "## 政策全文（可编辑）\n\n"
        "> 可在 Obsidian 中修改本段；完成后回到网站点击“导入 Obsidian 修改”。\n\n"
        f"{BODY_START}\n{policy.body.strip()}\n{BODY_END}"
    )


def _references(page):
    lines = ["## 引用与原文依据", ""]
    for index, citation in enumerate(page.current_revision.citations, start=1):
        quote = str(citation.get("quote", "")).strip().replace("\n", " ")
        lines += [
            f"### [{index}] {citation.get('title', '未命名政策')}",
            "",
            f"> {quote}",
            "",
            f"[查看政府原文]({citation.get('source_url', '')})",
            "",
        ]
    return "\n".join(lines).strip()


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def export_vault(destination=None):
    root = Path(destination or settings.OBSIDIAN_EXPORT_DIR).expanduser().resolve()
    if root == Path(root.anchor) or not root.name:
        raise ValueError("OBSIDIAN_EXPORT_DIR_UNSAFE")
    root.mkdir(parents=True, exist_ok=True)

    pages = list(
        KnowledgePage.objects.filter(status="published", current_revision__isnull=False)
        .select_related("current_revision")
        .prefetch_related("page_sources__policy")
        .order_by("page_type", "title", "id")
    )
    source_pages = defaultdict(list)
    for page in pages:
        for source in page.page_sources.all():
            source_pages[str(source.policy_id)].append(page)

    paths = {page.pk: _page_path(page) for page in pages}
    old_manifest = {}
    manifest_path = root / MANIFEST
    if manifest_path.exists():
        try:
            old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            old_manifest = {}
    old_entries = {entry.get("page_id"): entry for entry in old_manifest.get("entries", [])}
    for page in pages:
        old_entry = old_entries.get(str(page.pk), {})
        old_path = old_entry.get("path")
        old_target = root / old_path if old_path else None
        if (
            page.page_type == "policy"
            and old_target
            and _existing_file(old_target)
            and old_entry.get("sha256")
            and _sha256(old_target.read_text(encoding="utf-8")) != old_entry["sha256"]
        ):
            paths[page.pk] = Path(old_path)

    generated = []
    entries = []
    preserved_edits = []
    for page in pages:
        relative = paths[page.pk]
        policy = _policy_for_page(page) if page.page_type == "policy" else None
        related = {}
        for source in page.page_sources.all():
            for candidate in source_pages[str(source.policy_id)]:
                if candidate.pk != page.pk:
                    related[candidate.pk] = candidate.title
        links = ""
        if related:
            links = "\n\n## 关联知识页\n\n" + "\n".join(
                f"- [[{paths[page_id].with_suffix('').as_posix()}|{title}]]"
                for page_id, title in sorted(related.items(), key=lambda item: item[1])
            )
        body = page.current_revision.body.strip()
        if policy is not None:
            body = body + "\n\n" + _editable_policy_sections(policy)
        content = (
            _frontmatter(page, policy)
            + body
            + links
            + "\n\n"
            + _references(page)
            + "\n"
        )
        target = root / relative
        old_entry = old_entries.get(str(page.pk), {})
        old_path = old_entry.get("path")
        old_target = root / old_path if old_path else None
        edited = bool(
            policy
            and old_target
            and _existing_file(old_target)
            and old_entry.get("sha256")
            and _sha256(old_target.read_text(encoding="utf-8")) != old_entry["sha256"]
        )
        if edited:
            relative = Path(old_path)
            target = old_target
            content_hash = old_entry["sha256"]
            preserved_edits.append(relative.as_posix())
        else:
            _write(target, content)
            content_hash = _sha256(content)
        generated.append(relative.as_posix())
        entries.append(
            {
                "page_id": str(page.pk),
                "page_type": page.page_type,
                "path": relative.as_posix(),
                "sha256": content_hash,
                "policy_id": str(policy.pk) if policy else "",
                "policy_version": policy.version if policy else None,
            }
        )

    grouped = defaultdict(list)
    for page in pages:
        grouped[page.page_type].append(page)
    index_lines = [
        "# 政策知识库",
        "",
        "政策文件按地域和文件类型分类。政策页中的摘要、全文和中文属性可以编辑，修改后需在网站导入。",
        "",
    ]
    labels = {"policy": "政策知识页", "chain": "政策链", "topic": "专题", "region": "地区"}
    for page_type in ("policy", "chain", "topic", "region"):
        index_lines += [f"## {labels[page_type]}", ""]
        index_lines += [
            f"- [[{paths[page.pk].with_suffix('').as_posix()}|{page.title}]]"
            for page in grouped.get(page_type, [])
        ]
        index_lines.append("")
    _write(root / "00-首页.md", "\n".join(index_lines).strip() + "\n")
    generated.append("00-首页.md")

    schema = """# 使用说明

此 Vault 与政策平台数据库受控同步。

- PostgreSQL 是唯一正式事实来源。
- `01-政策文件` 中的中文属性、政策摘要和政策全文可以修改。
- 修改后回到网站“政策知识库”，点击“导入 Obsidian 修改”。
- 导入前系统会检查政策版本；有冲突时不会覆盖网站中的新版本。
- 未导入的本地修改不会被下一次自动导出覆盖。
- `[[页面链接]]` 用于 Obsidian 关系图和反向链接。
- 每个结论应回到页面底部的引用与政府原文核验。
- 个人研究笔记可放在 `90-个人研究笔记`，系统不会删除未登记的文件。
"""
    _write(root / "00-使用说明.md", schema)
    generated.append("00-使用说明.md")

    log_path = root / "00-更新记录.md"
    old_log = log_path.read_text(encoding="utf-8") if log_path.exists() else "# 导出记录\n"
    stamp = timezone.now().isoformat()
    _write(log_path, old_log.rstrip() + f"\n\n## [{stamp}] export\n\n导出 {len(pages)} 个知识页。\n")
    generated.append("00-更新记录.md")

    current = set(generated)
    for relative in old_manifest.get("files", []):
        candidate = (root / relative).resolve()
        if relative not in current and candidate.is_relative_to(root) and _existing_file(candidate):
            candidate.unlink()
    for old_folder in ("policies", "chains", "topics", "regions"):
        folder = root / old_folder
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    manifest = {
        "generated_at": stamp,
        "pages": len(pages),
        "files": sorted(current),
        "entries": entries,
    }
    _write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {
        "pages": len(pages),
        "generated_at": stamp,
        "vault": root.name,
        "preserved_edits": len(preserved_edits),
    }


def _frontmatter_values(content):
    if not content.startswith("---\n"):
        raise ValidationError("文件缺少 YAML 属性区。")
    boundary = content.find("\n---\n", 4)
    if boundary < 0:
        raise ValidationError("文件的 YAML 属性区没有正确结束。")
    values = yaml.safe_load(content[4:boundary]) or {}
    if not isinstance(values, dict):
        raise ValidationError("文件属性格式不正确。")
    return values


def _marked_value(content, start, end, label):
    left, separator, remainder = content.partition(start)
    if not separator:
        raise ValidationError(f"缺少“{label}”起始标记。")
    value, separator, _ = remainder.partition(end)
    if not separator:
        raise ValidationError(f"缺少“{label}”结束标记。")
    return value.strip()


def _choice_value(value, choices, field_label):
    mapping = {str(code): str(code) for code, _ in choices}
    mapping.update({str(label): str(code) for code, label in choices})
    if str(value) not in mapping:
        raise ValidationError(f"{field_label}不是系统支持的选项：{value}")
    return mapping[str(value)]


def _taxonomy_values(values, configured, field_label):
    if values in (None, ""):
        return []
    if isinstance(values, str):
        values = [item.strip() for item in re.split(r"[,，]", values) if item.strip()]
    if not isinstance(values, list):
        raise ValidationError(f"{field_label}必须是列表。")
    mapping = {key: key for key in configured}
    mapping.update({label: key for key, (label, _) in configured.items()})
    unknown = [str(item) for item in values if str(item) not in mapping]
    if unknown:
        raise ValidationError(f"{field_label}包含未知分类：{'、'.join(unknown)}")
    return list(dict.fromkeys(mapping[str(item)] for item in values))


@transaction.atomic
def _apply_policy_edit(entry, content, actor):
    policy = Policy.objects.select_for_update().get(pk=entry["policy_id"])
    if policy.version != entry.get("policy_version"):
        raise ValidationError(
            f"政策“{policy.title}”已在网站更新到版本 {policy.version}，请重新导出后再编辑。"
        )
    values = _frontmatter_values(content)
    if str(values.get("policy_id", "")) != str(policy.pk):
        raise ValidationError("文件中的政策ID与导出清单不一致。")
    publication_date = parse_date(str(values.get("发布日期", "")))
    if publication_date is None:
        raise ValidationError("发布日期必须使用 YYYY-MM-DD 格式。")
    title = str(values.get("政策名称", "")).strip()
    issuer = str(values.get("发文机关", "")).strip()
    document_number = str(values.get("发文字号", "")).strip()
    if not title or len(title) > 500:
        raise ValidationError("政策名称不能为空且不能超过500字。")
    if not issuer or len(issuer) > 200:
        raise ValidationError("发文机关不能为空且不能超过200字。")
    if len(document_number) > 200:
        raise ValidationError("发文字号不能超过200字。")
    validity_status = _choice_value(
        values.get("政策效力", "待核实"), ValidityStatus.choices, "政策效力"
    )
    document_type = _choice_value(
        values.get("文件类型", "待分类"), Policy.DocumentType.choices, "文件类型"
    )
    validity_evidence = str(values.get("政策效力依据", "")).strip()
    body = _marked_value(content, BODY_START, BODY_END, "政策全文")
    summary = _marked_value(content, SUMMARY_START, SUMMARY_END, "政策摘要")
    if len(body) < 30:
        raise ValidationError("政策全文不能少于30个字符。")
    if validity_status != "unverified" and (
        len(validity_evidence) < 5 or validity_evidence not in body
    ):
        raise ValidationError("非待核实的政策效力必须提供当前正文中的逐字依据。")
    changes = {
        "title": title,
        "issuer": issuer,
        "document_number": document_number,
        "publication_date": publication_date,
        "validity_status": validity_status,
        "validity_evidence": validity_evidence,
        "document_type": document_type,
        "business_domains": _taxonomy_values(
            values.get("业务领域", []), configured_domains(), "业务领域"
        ),
        "direction_tags": _taxonomy_values(
            values.get("方向标签", []), configured_tags(), "方向标签"
        ),
        "summary": summary,
        "body": body,
    }
    changed = {key: value for key, value in changes.items() if getattr(policy, key) != value}
    if not changed:
        return policy, []

    old_version = policy.version
    for key, value in changed.items():
        setattr(policy, key, value)
    policy.version += 1
    if "body" in changed:
        policy.content_hash = _sha256(policy.body)
    if "summary" in changed:
        policy.summary_method = "ai+manual"
        policy.summary_evidence = []
    scope = dict(policy.scope_evidence or {})
    previous = dict(scope.get("obsidian_correction") or {})
    locked = set(previous.get("locked_fields") or [])
    locked.update(key for key in changed if key not in {"body"})
    scope["obsidian_correction"] = {
        "actor_id": str(actor.pk),
        "at": timezone.now().isoformat(),
        "locked_fields": sorted(locked),
        "changed_fields": sorted(changed),
    }
    policy.scope_evidence = scope
    policy.save()
    record_field_values(
        policy,
        changed.keys(),
        source_type="human_correction",
        locked=True,
        actor=actor,
        evidence={
            field_name: validity_evidence
            for field_name in changed
            if field_name in {"validity_status", "validity_evidence"}
            and validity_evidence
        },
        evidence_location={
            field_name: {"kind": "obsidian_edit", "source_url": policy.source_url}
            for field_name in changed
        },
    )
    Evidence.objects.create(
        policy=policy,
        policy_version=policy.version,
        text=policy.body,
        location={"kind": "obsidian_edit", "source_url": policy.source_url},
        quote_hash=_sha256(policy.body),
    )
    PolicyEnrichment.objects.update_or_create(
        policy=policy,
        policy_version=policy.version,
        defaults={
            "status": "queued",
            "attempts": 0,
            "lease_until": None,
            "retry_at": None,
            "error_code": "",
            "prompt_version": "policy-enrichment-v6",
        },
    )
    AuditRecord.objects.create(
        actor=actor,
        action="policy.obsidian.corrected",
        object_id=policy.pk,
        details={
            "previous_version": old_version,
            "version": policy.version,
            "changed_fields": sorted(changed),
        },
    )
    return policy, sorted(changed)


def import_vault_edits(actor, destination=None):
    if not actor.is_active or not actor.has_perm("policies.change_policy"):
        raise PermissionDenied("需要政策审核权限。")
    root = Path(destination or settings.OBSIDIAN_EXPORT_DIR).expanduser().resolve()
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        raise ValidationError("尚未找到 Obsidian 导出清单，请先导出。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    imported, unchanged, conflicts = [], 0, []
    for entry in manifest.get("entries", []):
        if entry.get("page_type") != "policy" or not entry.get("policy_id"):
            continue
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            conflicts.append({"path": entry.get("path", ""), "reason": "文件不存在。"})
            continue
        content = path.read_text(encoding="utf-8")
        digest = _sha256(content)
        if digest == entry.get("sha256"):
            unchanged += 1
            continue
        try:
            policy, changed_fields = _apply_policy_edit(entry, content, actor)
        except (Policy.DoesNotExist, ValidationError, ValueError) as exc:
            conflicts.append({"path": entry["path"], "reason": str(exc)})
            continue
        entry["sha256"] = digest
        entry["policy_version"] = policy.version
        if changed_fields:
            imported.append(
                {"policy_id": str(policy.pk), "title": policy.title, "fields": changed_fields}
            )
        else:
            unchanged += 1
    manifest["entries"] = manifest.get("entries", [])
    manifest["last_imported_at"] = timezone.now().isoformat()
    _write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    if imported:
        from .tasks import enqueue_sync

        enqueue_sync(actor, force=True)
    return {
        "imported": len(imported),
        "unchanged": unchanged,
        "conflicts": conflicts,
        "items": imported,
    }
