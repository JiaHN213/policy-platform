"""Validated per-account and per-enterprise overrides, with platform fallbacks."""
from copy import copy

from .models import RecommendationSettings, ResearchSettings, ScopedSettings


def number(key, label, group, minimum, maximum):
    return {"key": key, "label": label, "group": group, "kind": "number", "min": minimum, "max": maximum}


def boolean(key, label, group):
    return {"key": key, "label": label, "group": group, "kind": "boolean"}


USER_FIELDS = [
    number("daily_limit", "24小时正常整理次数", "账号使用额度", 1, 100),
    number("active_limit", "同时处理任务数", "账号使用额度", 1, 5),
    number("workflow_daily_calls", "每日自动匹配模型请求上限", "账号使用额度", 1, 500),
]
ENTERPRISE_FIELDS = [
    boolean("research_allowed", "允许企业资料与项目整理", "企业资料整理"),
    boolean("matching_allowed", "允许政策匹配解读", "自动匹配与解读"),
    boolean("agent_enabled", "允许按资料缺口补查", "企业资料整理"),
    number("max_sources", "最多参考搜索网页数", "企业资料整理", 2, 8),
    number("agent_max_reads", "每次补查最多读取网页数", "企业资料整理", 1, 5),
    number("agent_max_calls", "每次补查最多模型请求数", "企业资料整理", 2, 10),
    number("agent_max_seconds", "每次补查时间预算（秒）", "企业资料整理", 60, 480),
    boolean("workflow_gap_fill", "匹配前尝试补充缺口", "自动匹配与解读"),
    number("workflow_max_policies", "每轮最多解读政策数", "自动匹配与解读", 1, 10),
    number("workflow_max_calls", "每轮模型请求上限", "自动匹配与解读", 1, 20),
    number("workflow_max_seconds", "每轮累计分析时间（秒）", "自动匹配与解读", 60, 900),
    number("workflow_concurrency", "每轮同时解读政策数", "自动匹配与解读", 1, 3),
    number("workflow_cache_hours", "有效结果复用时长（小时）", "自动匹配与解读", 0, 72),
    number("workflow_retry_limit", "临时故障重试次数", "自动匹配与解读", 0, 3),
    boolean("recommendation_enabled", "允许企业使用持续推荐", "持续推荐"),
    number("recommendation_batch_size", "每批检查政策数", "持续推荐", 1, 100),
    number("recommendation_daily_batches", "企业每日处理批数上限", "持续推荐", 1, 2000),
    number("recommendation_daily_model_calls", "企业每日自动解读次数上限", "持续推荐", 0, 1000),
    number("recommendation_model_calls_per_run", "每轮最多自动解读次数", "持续推荐", 0, 5),
    number("recommendation_aggregation_minutes", "资料修改合并等待（分钟）", "持续推荐", 1, 60),
    number("recommendation_retention_days", "运行明细保留天数", "持续推荐", 7, 365),
]


def overrides_for(*, user=None, profile=None):
    values = {}
    if user is not None:
        row = ScopedSettings.objects.filter(user=user).first()
        if row:
            values.update(row.overrides)
    if profile is not None:
        row = ScopedSettings.objects.filter(profile=profile).first()
        if row:
            values.update(row.overrides)
    return values


def research_configuration(user=None, profile=None, *, base=None):
    config = copy(base or ResearchSettings.objects.get_or_create(key="default")[0])
    config.active_limit = 2
    config.research_allowed = config.matching_allowed = True
    values = overrides_for(user=user, profile=profile)
    keys = {item["key"] for item in USER_FIELDS + ENTERPRISE_FIELDS if not item["key"].startswith("recommendation_")}
    for key in keys & values.keys():
        setattr(config, key, values[key])
    if "agent_enabled" in values:
        config.agent_all_organizations = True
    return config


def recommendation_configuration(profile=None, *, base=None):
    config = copy(base or RecommendationSettings.objects.get_or_create(key="default")[0])
    config.scope_overrides = {}
    if profile is not None:
        values = overrides_for(profile=profile)
        config.scope_overrides = {key.removeprefix("recommendation_"): value for key, value in values.items() if key.startswith("recommendation_")}
        for key, value in config.scope_overrides.items():
            setattr(config, key, value)
        # The platform service switch and shared resource caps remain safety controls.
        if base is not None:
            config.enabled = config.enabled and base.enabled
        else:
            config.enabled = config.enabled and RecommendationSettings.objects.get(key="default").enabled
        if "enabled" in config.scope_overrides:
            config.all_organizations = True
        if values.get("matching_allowed") is False:
            config.model_calls_per_run = 0
            config.scope_overrides["matching_allowed"] = False
    return config
