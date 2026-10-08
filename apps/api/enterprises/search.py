"""Private SearXNG gateway. Only the deployment-approved endpoint is reachable."""
import hashlib
import json
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.core.cache import cache


class SearchUnavailable(ValueError):
    pass


def validate_searxng_url(value):
    value = str(value).strip().rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
            or parsed.query or parsed.fragment or value != settings.SEARXNG_URL):
        raise SearchUnavailable("请使用部署配置中允许的 SearXNG 地址；本机默认 http://searxng:8080。其他地址需先由运维设置。")
    return value


def searxng_request(config, query=None, *, use_cache=True):
    base = validate_searxng_url(config.searxng_url)
    cooldown_key = "enterprise:searxng:cooldown:" + hashlib.sha256(base.encode()).hexdigest()
    key = "enterprise:searxng:" + hashlib.sha256(json.dumps(
        [base, query, config.max_sources], ensure_ascii=False).encode()).hexdigest()
    if query and use_cache:
        cached = cache.get(key)
        if cached is not None:
            return cached
    if query and cache.get(cooldown_key):
        raise SearchUnavailable("搜索引擎暂时限流或不可用，系统已暂停请求一分钟。请稍后再试，也可以直接填写官网、上传企业介绍或粘贴简介。")
    try:
        # Internal traffic must never inherit the host's outbound proxy.
        with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False, trust_env=False) as client:
            response = client.get(base + ("/search" if query else "/"),
                                  params={"q": query, "format": "json", "language": "zh-CN", "categories": "general"} if query else None)
            if response.status_code == 403 and query:
                raise SearchUnavailable("SearXNG 拒绝了搜索请求，请检查是否已启用 JSON 输出及访问限制。")
            if response.status_code == 429:
                cache.set(cooldown_key, True, timeout=60)
                raise SearchUnavailable("搜索服务请求过于频繁，请稍后重试。")
            response.raise_for_status()
            if response.is_redirect:
                raise SearchUnavailable("搜索服务发生跳转，请检查内部服务地址。")
            if not query:
                return {"connected": True, "message": "SearXNG 已连接；还需测试搜索以确认上游引擎可用。"}
            if len(response.content) > 2_000_000:
                raise SearchUnavailable("搜索服务返回内容过大，请缩小查询范围。")
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise SearchUnavailable("搜索服务未返回有效结果，请检查 JSON 接口配置。")
    except httpx.TimeoutException as exc:
        raise SearchUnavailable("搜索服务响应超时，请稍后重试，或改用官网、上传介绍和粘贴简介。") from exc
    except httpx.HTTPError as exc:
        raise SearchUnavailable("无法访问搜索服务，请检查 SearXNG 容器和服务地址。") from exc
    except (ValueError, TypeError) as exc:
        if isinstance(exc, SearchUnavailable):
            raise
        raise SearchUnavailable("搜索服务未返回可读取的 JSON，请检查服务配置。") from exc
    result = {"results": payload["results"][:40],
              "unresponsive_count": len(payload.get("unresponsive_engines") or [])}
    # Briefly stop all live queries during a total outage instead of amplifying
    # rate limits with repeated user/agent attempts. Successful cache hits remain usable.
    if not result["results"] and result["unresponsive_count"]:
        cache.set(cooldown_key, True, timeout=60)
        raise SearchUnavailable("搜索引擎暂时限流或不可用，系统已暂停请求一分钟。这不代表企业没有资料；可以填写官网、上传企业介绍或粘贴简介。")
    # Cache successful public queries only; never persist private materials.
    if result["results"] and use_cache:
        cache.set(key, result, timeout=300)
    return result
