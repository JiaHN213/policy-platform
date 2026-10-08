"""Read-only aggregate baseline; execute via stdin inside the existing API container."""
# Django must be initialized before importing model classes in this standalone probe.
# ruff: noqa: E402
import json
import logging
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

sys.path.insert(0, "/app/apps/api")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()
logging.getLogger("httpx").setLevel(logging.WARNING)

import httpx
from core.ai_runtime import get_ai_profile
from django.db import connection, transaction
from django.db.models import Count
from enterprises.models import ResearchRun
from knowledge.models import KnowledgeBuild
from policies.models import PolicyEnrichment


def counts(model):
    return dict(model.objects.values("status").annotate(n=Count("pk")).values_list("status", "n"))


report = {"captured_at": datetime.now(timezone.utc).isoformat(), "read_only": True}
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
    profiles = [get_ai_profile(p) for p in (
        "enterprise", "search", "review", "wiki_relations", "wiki_synthesis",
    )]
    endpoint_groups = {}
    report["models"] = []
    for profile in profiles:
        parsed = urlsplit(profile.base_url)
        endpoint = f"{parsed.scheme}://{parsed.netloc}" if profile.is_local else profile.base_url.rstrip("/")
        group = endpoint_groups.setdefault(endpoint, len(endpoint_groups) + 1)
        report["models"].append({
            "purpose": profile.purpose, "model": profile.model,
            "enabled": profile.enabled, "configured": profile.configured,
            "local": profile.is_local, "endpoint_group": group,
            "local_hostname": parsed.hostname if profile.is_local else None,
            "configured_concurrency": profile.concurrency,
        })
    report["tasks"] = {
        "enterprise": counts(ResearchRun), "review": counts(PolicyEnrichment),
        "wiki": counts(KnowledgeBuild),
    }
    report["enterprise_timings"] = {}
    for kind in ("company", "project", "explanation"):
        rows = ResearchRun.objects.filter(
            kind=kind, status="completed", started_at__isnull=False, finished_at__isnull=False,
        ).order_by("-finished_at").values_list("started_at", "finished_at")[:100]
        durations = sorted((end - start).total_seconds() for start, end in rows if end >= start)
        report["enterprise_timings"][kind] = {
            "sample_size": len(durations), "window": "latest_100_completed",
            "median_seconds": round(statistics.median(durations), 3) if durations else None,
            "p95_seconds": round(durations[math.ceil(.95 * len(durations)) - 1], 3) if durations else None,
        }
    report["review_wiki_duration"] = "unavailable: no dedicated execution start/end fields"

report["ollama_loaded_models"] = []
seen = set()
for profile in profiles:
    if not profile.is_local or not profile.configured:
        continue
    parsed = urlsplit(profile.base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base in seen:
        continue
    seen.add(base)
    try:
        result = httpx.get(base + "/api/ps", timeout=8, trust_env=False)
        result.raise_for_status()
        models = result.json().get("models", [])
        report["ollama_loaded_models"].append({"hostname": parsed.hostname,
            "status": "available", "loaded_count": len(models)})
        report["ollama_loaded_models"].extend({
            key: model.get(key) for key in ("name", "context_length", "size", "size_vram")
        } for model in result.json().get("models", []))
        detail = httpx.post(base + "/api/show", json={"model": profile.model},
                            timeout=8, trust_env=False)
        detail.raise_for_status()
        params = detail.json().get("parameters", "")
        report.setdefault("ollama_model_defaults", []).append({
            "model": profile.model,
            "context_parameter": next((line.strip() for line in params.splitlines()
                                       if line.strip().startswith("num_ctx")), None),
            "note": "模型默认值；未加载时不能证明下一次请求实际使用的上下文长度",
        })
    except (httpx.HTTPError, ValueError) as exc:
        report["ollama_loaded_models"].append({"hostname": parsed.hostname,
            "status": "unavailable", "error_type": type(exc).__name__})
print(json.dumps(report, ensure_ascii=False, indent=2))
