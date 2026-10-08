"""Shared typed profile fields; business tags reuse the versioned configuration."""
import re

from core.business_config import get_config
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from rest_framework import serializers

FIELDS = {
    "credit_code": "统一社会信用代码", "province": "所在省份", "city": "所在城市",
    "registered_city": "企业注册城市（可选，与办公城市区分）",
    "interest_regions": "关注地区（可选多个，不确定可留空）",
    "website": "企业官网", "business_summary": "主营业务", "business_domains": "业务领域",
    "direction_tags": "技术与政策方向", "capabilities": "能力与资质线索",
    "history_projects": "公开历史业绩",
    "subject_type": "主体类型（企业／事业单位／社会组织等）",
    "annual_revenue_wan": "营业收入（万元，可选）", "annual_revenue_year": "营业收入对应年度",
    "project_stage": "项目阶段（拟建／在建／已完工等）", "investment_wan": "项目总投资（万元，可选）",
}
LIST_FIELDS = {"business_domains", "direction_tags", "capabilities", "history_projects", "interest_regions"}
PROJECT_ONLY_FIELDS = {"project_stage", "investment_wan"}
COMPANY_FIELDS = {key: label for key, label in FIELDS.items() if key not in PROJECT_ONLY_FIELDS}


def tag_options():
    config = get_config("business_scope")
    return {key: [{"value": item["code"], "label": item["label"]} for item in config[key]
                  if item.get("enabled", True)] for key in ("business_domains", "direction_tags")}


def validate_data(value, *, project=False):
    allowed = FIELDS if project else COMPANY_FIELDS
    if not isinstance(value, dict) or set(value) - allowed.keys():
        raise serializers.ValidationError("画像包含不支持的字段。")
    options = tag_options()
    for key, item in value.items():
        if key in LIST_FIELDS:
            if not isinstance(item, list) or len(item) > 30 or any(
                not isinstance(v, str) or len(v) > 500 for v in item
            ):
                raise serializers.ValidationError(f"{FIELDS[key]}最多填写30项，每项不超过500字。")
            if key in options and set(item) - {v["value"] for v in options[key]}:
                raise serializers.ValidationError(f"请从现有{FIELDS[key]}中选择。")
        elif not isinstance(item, str) or len(item) > 2000:
            raise serializers.ValidationError(f"{FIELDS[key]}请填写不超过2000字的文字。")
        if key == "credit_code" and item and not re.fullmatch(r"[A-Z0-9]{18}", item):
            raise serializers.ValidationError("统一社会信用代码应为18位大写字母或数字；不确定可留空。")
        if key in {"annual_revenue_wan", "investment_wan"} and item and not re.fullmatch(r"\d{1,12}(?:\.\d{1,4})?", item):
            raise serializers.ValidationError(f"{FIELDS[key]}请填写非负数字，单位为万元。")
        if key == "annual_revenue_year" and item and not re.fullmatch(r"(?:19|20)\d{2}", item):
            raise serializers.ValidationError("营业收入年度请填写四位年份。")
        if key == "website" and item:
            try:
                URLValidator(schemes=["http", "https"])(item)
            except DjangoValidationError as exc:
                raise serializers.ValidationError("请填写以 http:// 或 https:// 开头的企业官网，不确定可留空。") from exc
    return value
