"""Evidence-based intake backed by versioned business configuration."""

import re

from core.business_config import get_config


def _maps(*, database=True):
    config = get_config("business_scope", database=database)
    domains = {
        item["code"]: (item["label"], item["terms"])
        for item in config["business_domains"]
        if item.get("enabled", True)
    }
    tags = {
        item["code"]: (item["label"], item["terms"])
        for item in config["direction_tags"]
        if item.get("enabled", True)
    }
    return config, domains, tags


# Compatibility exports for serializers and modules that build choices at import time.
_BASE_CONFIG, DOMAINS, TAGS = _maps(database=False)
SEARCH_TERMS = sorted(
    {term for _, terms in DOMAINS.values() for term in terms}
    | set(_BASE_CONFIG.get("primary_collection_terms", []))
)


def configured_domains():
    return _maps()[1]


def configured_tags():
    return _maps()[2]


def assess_scope(title, body):
    """Tags alone never qualify a document; save exact applicability snippets."""
    config, domains_config, tags_config = _maps()
    text = title + "\n" + body
    domains, evidence = [], []
    for key, (_, terms) in domains_config.items():
        for term in terms:
            offset = text.find(term)
            if offset >= 0:
                domains.append(key)
                left = max(text.rfind("。", 0, offset), text.rfind("\n", 0, offset)) + 1
                right = text.find("。", offset)
                quote = text[
                    max(left, offset - 180) : min(
                        right + 1 if right >= 0 else len(text), offset + 200
                    )
                ]
                evidence.append(
                    {
                        "domain": key,
                        "term": term,
                        "quote": quote[:500],
                        "location": "title" if offset < len(title) else "body",
                    }
                )
                break
    tags = [
        key for key, (_, terms) in tags_config.items() if any(term in text for term in terms)
    ]
    patterns = config.get("negative_title_patterns", [])
    news = any(re.search(pattern, title) for pattern in patterns)
    if news:
        decision, reason = "excluded", "普通新闻、工作动态或人事任免，不属于政策收录类型"
    elif domains and (
        any(term in title for _, terms in domains_config.values() for term in terms)
        or any(re.search(pattern, body) for pattern in config.get("applicability_patterns", []))
    ):
        decision, reason = "included", "原文明确涉及水务业务领域；方向标签不独立触发纳入"
    elif domains:
        decision, reason = (
            "needs_review",
            "只检测到水务词语，尚不足以确认适用于水务企业、设施或项目",
        )
    else:
        decision, reason = "excluded", "未找到明确适用于水务企业、设施或项目的业务范围依据"
    return {
        "industry": "water_environment",
        "business_domains": domains,
        "direction_tags": tags,
        "decision": decision,
        "reason": reason,
        "evidence": evidence,
        "rule_version": config.get("version", "water-v1"),
    }
