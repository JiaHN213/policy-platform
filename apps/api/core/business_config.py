"""Versioned business configuration with a Git-tracked JSON baseline."""

import hashlib
import json
import time
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db import OperationalError, ProgrammingError


class BusinessConfigError(ValueError):
    pass


_runtime_cache = {"expires": 0.0, "release": None, "documents": {}}


def config_root() -> Path:
    return Path(settings.BASE_DIR) / "configs"


@lru_cache(maxsize=1)
def manifest() -> dict:
    return _read_json(config_root() / "manifest.json")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BusinessConfigError(f"无法读取业务配置：{path.name}") from exc
    if not isinstance(value, dict):
        raise BusinessConfigError(f"业务配置顶层必须是对象：{path.name}")
    return value


def baseline_documents() -> dict[str, dict]:
    root = config_root().resolve()
    documents = {}
    for key, relative in manifest().get("files", {}).items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise BusinessConfigError(f"配置路径越界：{key}")
        documents[key] = _read_json(path)
    return documents


def _published_document(key: str):
    """Return the current DB revision when migrations are available."""
    try:
        from core.models import SystemConfigDocument, SystemConfigRelease

        now = time.monotonic()
        if now < _runtime_cache["expires"]:
            return _runtime_cache["documents"].get(key)
        release = SystemConfigRelease.objects.filter(
            status=SystemConfigRelease.Status.PUBLISHED
        ).order_by("-published_at", "-created_at").first()
        if not release:
            _runtime_cache.update(expires=now + 15, release=None, documents={})
            return None
        documents = {
            item.key: item.content for item in SystemConfigDocument.objects.filter(release=release)
        }
        _runtime_cache.update(
            expires=now + 15, release=str(release.pk), documents=documents
        )
        return documents.get(key)
    except (OperationalError, ProgrammingError, RuntimeError):
        return None


def get_config(key: str, *, database=True) -> dict:
    if database:
        configured = _published_document(key)
        if configured is not None:
            if key == "wiki_relations":
                return {"automatic_repair": False, **configured}
            return configured
    documents = baseline_documents()
    if key not in documents:
        raise BusinessConfigError(f"未知业务配置：{key}")
    return documents[key]


def config_version() -> str:
    try:
        from core.models import SystemConfigRelease

        release = (
            SystemConfigRelease.objects.filter(status=SystemConfigRelease.Status.PUBLISHED)
            .order_by("-published_at", "-created_at")
            .first()
        )
        if release:
            return release.version
    except (OperationalError, ProgrammingError, RuntimeError):
        pass
    return str(manifest().get("release_version", "baseline"))


def checksum(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_documents(documents: dict[str, dict]) -> list[str]:
    document_labels = {
        "system_taxonomies": "分类名称",
        "business_scope": "行业范围与关键词",
        "document_classification": "文件类型识别",
        "opportunity_identification": "政策机会识别",
        "policy_validity": "政策效力判断",
        "wiki_relations": "政策关系识别",
        "opportunity_rules": "政策机会判断原则",
    }
    group_labels = {
        "industries": "核心行业",
        "document_types": "政策文件类型",
        "source_grades": "来源等级",
        "geographic_levels": "地域层级",
        "validity_statuses": "政策效力状态",
        "opportunity_levels": "政策机会级别",
        "opportunity_categories": "政策机会分类",
        "opportunity_statuses": "政策机会状态",
        "document_roles": "文件角色",
        "acquisition_methods": "取得方式",
        "verification_statuses": "核验状态",
        "relation_kinds": "政策关系类型",
        "business_domains": "核心业务领域",
        "direction_tags": "技术与政策方向",
    }
    errors = []
    repair_enabled = documents.get("wiki_relations", {}).get("automatic_repair", False)
    if not isinstance(repair_enabled, bool):
        errors.append("关系自动补查开关必须为启用或停用。")
    required = set(manifest().get("files", {}))
    missing = sorted(required - set(documents))
    if missing:
        errors.append("缺少设置模块：" + "、".join(document_labels.get(key, "业务设置") for key in missing))
    taxonomies = documents.get("system_taxonomies", {})
    baseline_taxonomies = _read_json(config_root() / "taxonomy" / "system_taxonomies.json")
    for group in (
        "industries",
        "document_types",
        "source_grades",
        "geographic_levels",
        "validity_statuses",
        "opportunity_levels",
        "opportunity_categories",
        "opportunity_statuses",
        "document_roles",
        "acquisition_methods",
        "verification_statuses",
        "relation_kinds",
    ):
        items = taxonomies.get(group)
        if not isinstance(items, list) or not items:
            errors.append(f"{group_labels[group]}至少需要保留一项")
            continue
        codes = [item.get("code") for item in items if isinstance(item, dict)]
        if any(not code for code in codes) or len(codes) != len(items):
            errors.append(f"{group_labels[group]}中存在内容不完整的项目")
        if len(set(codes)) != len(codes):
            errors.append(f"{group_labels[group]}中存在重复项目")
        stable_codes = {item["code"] for item in baseline_taxonomies.get(group, [])}
        if set(codes) != stable_codes:
            errors.append(f"{group_labels[group]}只能修改显示名称，不能新增或删除系统基础项目")
    scope = documents.get("business_scope", {})
    baseline_scope = _read_json(config_root() / "dictionaries" / "business_scope.json")
    for group in ("business_domains", "direction_tags"):
        items = scope.get(group)
        if not isinstance(items, list) or not items:
            errors.append(f"{group_labels[group]}至少需要保留一项")
            continue
        for item in items:
            if not item.get("code") or not item.get("label") or not item.get("terms"):
                errors.append(f"{group_labels[group]}中存在缺少名称或关键词的项目")
                break
        baseline_codes = {item["code"] for item in baseline_scope.get(group, [])}
        current_codes = {item.get("code") for item in items}
        if not baseline_codes.issubset(current_codes):
            errors.append(f"{group_labels[group]}中系统已经使用的项目不能删除")
    opportunity = documents.get("opportunity_identification", {})
    if opportunity.get("meta", {}).get("core_rule") is None:
        errors.append("政策机会识别的核心判断原则不能为空")
    return errors


def clear_config_cache():
    manifest.cache_clear()
    _runtime_cache.update(expires=0.0, release=None, documents={})
