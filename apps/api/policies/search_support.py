"""Short, source-bound evidence and private caches for interactive search."""
import hashlib
import json
import re

from accounts.services import access_decision
from core.ai_runtime import get_ai_profile, profile_signature
from core.business_config import config_version
from core.errors import Conflict
from django.core import signing
from django.core.cache import caches
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .catalog import formal_policies

SALT = "policy-search-summary-v1"


def identity(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def cache_read(key):
    try:
        return caches["search"].get(key)
    except Exception:
        return None  # Redis failure must not make the policy library unavailable.


def cache_write(key, value, seconds):
    try:
        caches["search"].set(key, value, seconds)
    except Exception:
        pass


def intent_for(user, question, parse):
    profile = get_ai_profile("search")
    key = "intent:" + identity(user.pk, question.strip(), profile_signature(profile), config_version(), timezone.localdate(), "v1")
    result = cache_read(key)
    if result is None:
        result = parse(question)
        cache_write(key, result, 3600)
    return result


def excerpts(body, terms, limit=1200):
    # Split long paragraphs into literal windows; never rewrite evidence.
    spans = []
    for match in re.finditer(r"[^\n]+", body):
        start, end = match.span()
        for offset in range(start, end, 400):
            spans.append((offset, body[offset:min(offset + 500, end)]))
    terms = [term.casefold() for term in terms if term]
    ranked = sorted(spans, key=lambda item: (-sum(item[1].casefold().count(term) for term in terms), item[0]))
    chosen, length = [], 0
    for start, text in ranked:
        if any(start < other + len(value) and other < start + len(text) for other, value in chosen):
            continue
        text = text[:limit - length]
        if not text:
            break
        chosen.append((start, text))
        length += len(text) + 1
        if length >= limit:
            break
    return "\n".join(text for _, text in sorted(chosen))


def check_sources(user, citations):
    user.refresh_from_db()
    if not user.is_active or not all(access_decision(user, capability)["allowed"] for capability in ("policy_search", "policy_detail")):
        raise PermissionDenied("当前账号不能查看这些政策。")
    current = {str(policy.pk): policy for policy in formal_policies(user).filter(pk__in=[c["policy_id"] for c in citations])}
    if any(c["policy_id"] not in current or current[c["policy_id"]].version != c["version"] for c in citations):
        raise Conflict("政策内容或可见范围已变化，请重新搜索。")
    return current


def issue_ticket(user, question, result):
    return signing.dumps({"user": user.pk, "q": question, "terms": result["keywords"] + [result["applied_filters"].get("topic", "")],
        "citations": result["citations"]}, salt=SALT, compress=True)


def read_ticket(user, token):
    try:
        data = signing.loads(token, salt=SALT, max_age=600)
    except signing.BadSignature as exc:
        raise ValidationError("搜索结果已过期或无效，请重新搜索。") from exc
    if data["user"] != user.pk:
        raise PermissionDenied("只能查看本人搜索结果的归纳。")
    return data


def summarize(user, question, result, generate):
    current = check_sources(user, result["citations"])
    profile = get_ai_profile("search_summary")
    empty = {"claims": [], "answer": "已找到政策，请直接阅读原文；归纳暂未生成。"}
    if not profile.configured:
        return empty
    key = "summary:" + identity(user.pk, user.permission_version, question, result["citations"], result["evidence"], profile_signature(profile), config_version())
    cached = cache_read(key)
    if cached is not None:
        return cached
    try:
        generated = generate(question, result["evidence"])
    except Exception:
        check_sources(user, result["citations"])
        return empty
    current = check_sources(user, result["citations"])
    claims = []
    for claim in generated.get("claims", []):
        policy_id, quote = claim.get("evidence_id"), claim.get("quote")
        if quote and policy_id in current and quote in result["evidence"].get(policy_id, "") and quote in current[policy_id].body:
            claims.append({"text": quote, "policy_id": policy_id})
    response = {"claims": claims[:3], "answer": "以下要点来自本页前5份政策的原文，请核对引用。" if claims else "检索到文件，但没有可验证的归纳内容，请阅读原文。"}
    if claims:
        cache_write(key, response, 600)
    return response
