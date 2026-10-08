"""Grounded, versioned policy knowledge pages built from the formal policy database."""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from policies.business_scope import DOMAINS, TAGS
from policies.models import Policy, PolicyRelation

from .models import (
    KnowledgeLintIssue,
    KnowledgePage,
    KnowledgePageSource,
    KnowledgeRevision,
)
from .relations import audit_relations, relation_runtime_signature
from .synthesis import enabled as llm_synthesis_enabled
from .synthesis import synthesize, template_result


@dataclass
class PageSpec:
    key: str
    slug: str
    page_type: str
    title: str
    abstract: str
    body: str
    policies: list[Policy]
    citations: list[dict]
    extra_fingerprint: list | dict | None = None


def formal_policies():
    return Policy.objects.filter(
        status="published",
        source_grade__in=Policy.FORMAL_SOURCE_GRADES,
        is_demo=False,
    )


def current_relations():
    policies = formal_policies()
    return (
        PolicyRelation.objects.filter(
            verification_status="verified",
            evidence_version=F("evidence_policy__version"),
            from_policy__in=policies,
            to_policy__in=policies,
            evidence_policy__in=policies,
        )
        .select_related("from_policy", "to_policy", "evidence_policy")
        .order_by("from_policy__publication_date", "created_at", "id")
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def knowledge_fingerprint():
    policies = list(
        formal_policies()
        .order_by("id")
        .values(
            "id",
            "version",
            "content_hash",
            "summary",
            "business_domains",
            "direction_tags",
            "validity_status",
        )
    )
    relations = list(
        current_relations().values(
            "id",
            "from_policy_id",
            "to_policy_id",
            "kind",
            "evidence_policy_id",
            "evidence_version",
            "evidence_quote",
            "updated_at",
        )
    )
    return _digest({"policies": policies, "relations": relations, "schema": "wiki-v2",
                    "relation_runtime": relation_runtime_signature()})


def _quote(policy):
    for item in policy.summary_evidence or []:
        quote = str(item.get("quote", "")).strip()
        if quote and quote in policy.body:
            return quote[:700]
    for item in (policy.scope_evidence or {}).get("ai_review", {}).get("evidence", []):
        quote = str(item.get("quote", "")).strip()
        if quote and quote in policy.body:
            return quote[:700]
    return policy.body.strip()[:700]


def _citation(policy, quote=None, purpose="政策依据"):
    return {
        "policy_id": str(policy.pk),
        "version": policy.version,
        "title": policy.title,
        "document_number": policy.document_number,
        "quote": (quote or _quote(policy)).strip(),
        "source_url": policy.source_url,
        "purpose": purpose,
    }


def _summary(policy):
    return (policy.summary or policy.body[:500]).strip().replace("\n", " ")


def _policy_spec(policy, relations):
    citations = [_citation(policy, purpose="本页主要政策原文")]
    lines = [
        f"# {policy.title}",
        "",
        "## 文件概况",
        "",
        f"- 发文机关：{policy.issuer}",
        f"- 发文字号：{policy.document_number or '原文未标明'}",
        f"- 发布日期：{policy.publication_date.isoformat()}",
        f"- 地域：{policy.region}",
        f"- 政策效力：{policy.get_validity_status_display()}",
        f"- 来源等级：{policy.get_source_grade_display()}",
        "",
        "## 政策摘要",
        "",
        _summary(policy) + " [1]",
    ]
    keywords = [str(item.get("term", "")) for item in policy.structured_keywords if item.get("term")]
    if keywords:
        lines += ["", "## 结构化关键词", "", "、".join(keywords[:20])]
    related = [r for r in relations if policy.pk in {r.from_policy_id, r.to_policy_id}]
    if related:
        lines += ["", "## 政策关系", ""]
        for relation in related:
            other = relation.to_policy if relation.from_policy_id == policy.pk else relation.from_policy
            direction = "指向" if relation.from_policy_id == policy.pk else "来自"
            citations.append(
                _citation(relation.evidence_policy, relation.evidence_quote, "关系证据")
            )
            lines.append(
                f"- {direction}《{other.title}》：{relation.get_kind_display()} [{len(citations)}]"
            )
    lines += ["", "## 原文入口", "", policy.source_url]
    return PageSpec(
        key=f"policy:{policy.pk}",
        slug=f"policy-{policy.pk}",
        page_type="policy",
        title=policy.title,
        abstract=_summary(policy)[:260],
        body="\n".join(lines),
        policies=[policy],
        citations=citations,
        extra_fingerprint=[str(r.pk) + ":" + str(r.updated_at) for r in related],
    )


def _collection_spec(key, slug, page_type, title, policies, description):
    ordered = sorted(policies, key=lambda p: (p.publication_date, str(p.pk)), reverse=True)
    citations, lines = [], [f"# {title}", "", description, "", "## 收录政策", ""]
    for policy in ordered[:100]:
        citations.append(_citation(policy))
        lines += [
            f"### {policy.title}",
            "",
            f"{policy.publication_date.isoformat()} · {policy.issuer} · {policy.get_validity_status_display()}",
            "",
            _summary(policy)[:700] + f" [{len(citations)}]",
            "",
        ]
    return PageSpec(
        key=key,
        slug=slug,
        page_type=page_type,
        title=title,
        abstract=f"汇总 {len(ordered)} 份当前正式政策，并保留每项结论的原文出处和版本。",
        body="\n".join(lines),
        policies=ordered[:100],
        citations=citations,
    )


def _chain_specs(relations):
    parent = {}

    def find(value):
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for relation in relations:
        union(str(relation.from_policy_id), str(relation.to_policy_id))
    groups = defaultdict(list)
    for relation in relations:
        groups[find(str(relation.from_policy_id))].append(relation)
    specs = []
    for root, edges in groups.items():
        by_id = {}
        for edge in edges:
            by_id[str(edge.from_policy_id)] = edge.from_policy
            by_id[str(edge.to_policy_id)] = edge.to_policy
        policies = sorted(by_id.values(), key=lambda p: (p.publication_date, str(p.pk)))
        citations = []
        lines = [f"# 政策链：{policies[0].title}", "", "## 文件顺序", ""]
        for policy in policies:
            lines.append(f"- {policy.publication_date.isoformat()} 《{policy.title}》")
        lines += ["", "## 已发现关系", ""]
        edge_fingerprint = []
        for edge in edges:
            citations.append(_citation(edge.evidence_policy, edge.evidence_quote, "关系证据"))
            lines.append(
                f"- 《{edge.from_policy.title}》 → **{edge.get_kind_display()}** → 《{edge.to_policy.title}》 [{len(citations)}]"
            )
            edge_fingerprint.append(
                [str(edge.pk), edge.kind, edge.evidence_version, edge.evidence_quote]
            )
        specs.append(
            PageSpec(
                key=f"chain:{root}",
                slug=f"chain-{root}",
                page_type="chain",
                title=f"政策链：{policies[0].title}",
                abstract=f"由 {len(policies)} 份政策和 {len(edges)} 条有向关系组成。",
                body="\n".join(lines),
                policies=policies,
                citations=citations,
                extra_fingerprint=edge_fingerprint,
            )
        )
    return specs


def page_specs():
    policies = list(formal_policies().order_by("publication_date", "id"))
    relations = list(current_relations())
    specs = [_policy_spec(policy, relations) for policy in policies]
    for key, (label, _) in DOMAINS.items():
        matches = [policy for policy in policies if key in policy.business_domains]
        if matches:
            specs.append(
                _collection_spec(
                    f"topic:domain:{key}",
                    f"topic-domain-{key}",
                    "topic",
                    f"业务专题：{label}",
                    matches,
                    f"本页汇总与“{label}”直接相关的正式政策。",
                )
            )
    for key, (label, _) in TAGS.items():
        matches = [policy for policy in policies if key in policy.direction_tags]
        if matches:
            specs.append(
                _collection_spec(
                    f"topic:direction:{key}",
                    f"topic-direction-{key}",
                    "topic",
                    f"方向专题：{label}",
                    matches,
                    f"本页汇总带有“{label}”方向标签的正式政策；方向标签本身不代表适用资格。",
                )
            )
    regions = defaultdict(list)
    for policy in policies:
        region = policy.city or policy.province or policy.region or "全国"
        regions[region].append(policy)
    for region, matches in regions.items():
        token = hashlib.sha1(region.encode("utf-8")).hexdigest()[:16]
        specs.append(
            _collection_spec(
                f"region:{region}",
                f"region-{token}",
                "region",
                f"地区专题：{region}",
                matches,
                f"本页汇总发布地域为“{region}”的正式政策。",
            )
        )
    specs.extend(_chain_specs(relations))
    return specs


def _input_hash(spec):
    return _digest(
        {
            "schema": "wiki-llm-grounded-v2",
            "key": spec.key,
            "body": spec.body,
            "sources": [
                [str(policy.pk), policy.version, policy.content_hash] for policy in spec.policies
            ],
            "extra": spec.extra_fingerprint,
        }
    )


@transaction.atomic
def publish_spec(spec, digest, content):
    page, _ = KnowledgePage.objects.select_for_update().get_or_create(
        key=spec.key,
        defaults={"slug": spec.slug, "page_type": spec.page_type, "title": spec.title},
    )
    changed = not page.current_revision or page.current_revision.input_hash != digest
    if changed:
        number = (page.revisions.order_by("-number").values_list("number", flat=True).first() or 0) + 1
        revision = KnowledgeRevision.objects.create(
            page=page,
            number=number,
            body=content.body,
            citations=spec.citations,
            source_versions={str(p.pk): p.version for p in spec.policies},
            input_hash=digest,
            model=content.model,
            prompt_version=content.prompt_version,
        )
        page.current_revision = revision
    page.slug = spec.slug
    page.page_type = spec.page_type
    page.title = spec.title
    page.abstract = content.abstract
    page.status = "published"
    page.source_count = len(spec.policies)
    page.built_at = timezone.now()
    page.save()
    KnowledgePageSource.objects.filter(page=page).delete()
    KnowledgePageSource.objects.bulk_create(
        [
            KnowledgePageSource(page=page, policy=p, policy_version=p.version)
            for p in spec.policies
        ]
    )
    return page, changed


def lint_page(page):
    revision = page.current_revision
    if revision is None:
        page.status = "stale"
        page.save(update_fields=["status", "updated_at"])
        return 1
    KnowledgeLintIssue.objects.filter(page=page, revision=revision, resolved_at__isnull=True).update(
        resolved_at=timezone.now()
    )
    issues = []
    for index, citation in enumerate(revision.citations):
        policy = Policy.objects.filter(pk=citation.get("policy_id")).first()
        if policy is None:
            issues.append(("MISSING_SOURCE", "引用的政策已不存在", index))
        elif policy.status != "published" or policy.source_grade not in Policy.FORMAL_SOURCE_GRADES:
            issues.append(("HIDDEN_SOURCE", "引用的政策已撤下或不再是正式来源", index))
        elif policy.version != citation.get("version"):
            issues.append(("STALE_VERSION", "引用的政策版本已经变化", index))
        elif not citation.get("quote") or citation["quote"] not in policy.body:
            issues.append(("INVALID_QUOTE", "引用文字不在当前政策正文中", index))
    source_versions = revision.source_versions or {}
    for source in page.page_sources.select_related("policy"):
        if source.policy.version != source_versions.get(str(source.policy_id)):
            issues.append(("STALE_PAGE_SOURCE", "知识页来源版本已经变化", None))
    KnowledgeLintIssue.objects.bulk_create(
        [
            KnowledgeLintIssue(
                page=page,
                revision=revision,
                code=code,
                message=message,
                citation_index=index,
            )
            for code, message, index in issues
        ]
    )
    next_status = "stale" if issues else "published"
    if page.status != next_status:
        page.status = next_status
        page.save(update_fields=["status", "updated_at"])
    return len(issues)


def lint_all():
    pages = KnowledgePage.objects.exclude(status="archived").select_related("current_revision")
    issue_count = sum(lint_page(page) for page in pages)
    return {"pages": pages.count(), "issues": issue_count}


def sync_affected(policy_ids):
    return sync_all(affected_policy_ids=policy_ids)


def sync_all(affected_policy_ids=None):
    targeted = affected_policy_ids is not None
    affected = set(affected_policy_ids or [])
    relation_audit = {} if targeted else audit_relations()
    # A change arriving during synthesis must remain visible to the next build.
    input_hash = knowledge_fingerprint()
    specs = page_specs()
    old_page_ids = list(KnowledgePageSource.objects.filter(policy_id__in=affected)
                        .values_list("page_id", flat=True)) if targeted else []
    if targeted:
        specs = [spec for spec in specs if any(p.pk in affected for p in spec.policies)]
    active_keys, changed = set(), 0
    counts = defaultdict(int)
    llm_used = llm_synthesis_enabled()
    llm_revisions = 0
    template_revisions = 0
    failed_pages = []
    for spec in specs:
        active_keys.add(spec.key)
        digest = _input_hash(spec)
        current = (
            KnowledgePage.objects.filter(key=spec.key)
            .select_related("current_revision")
            .first()
        )
        if current and current.current_revision and current.current_revision.input_hash == digest:
            counts[spec.page_type] += 1
            continue
        previous_body = current.current_revision.body if current and current.current_revision else ""
        if current:
            KnowledgePage.objects.filter(pk=current.pk).update(status="stale")
        use_llm = llm_used and spec.page_type in {"chain", "topic", "region"} and len(
            spec.policies
        ) > 1
        try:
            content = synthesize(spec, previous_body) if use_llm else template_result(spec)
        except Exception as exc:
            from knowledge.synthesis import synthesis_failure

            failed_pages.append(
                {
                    "key": spec.key,
                    "title": spec.title,
                    "reason": synthesis_failure(exc) + "旧修订未被覆盖。",
                }
            )
            counts[spec.page_type] += 1
            continue
        with transaction.atomic():
            if targeted:
                live = list(Policy.objects.select_for_update().filter(
                    pk__in=[p.pk for p in spec.policies]).order_by("id"))
                if ({p.pk: p.version for p in live} != {p.pk: p.version for p in spec.policies}
                        or knowledge_fingerprint() != input_hash):
                    failed_pages.append({"key": spec.key, "reason": "来源或关系已变化，保留旧修订并等待下次更新。"})
                    continue
            _, created_revision = publish_spec(spec, digest, content)
        changed += int(created_revision)
        llm_revisions += int(created_revision and use_llm)
        template_revisions += int(created_revision and not use_llm)
        counts[spec.page_type] += 1
    obsolete = KnowledgePage.objects.exclude(key__in=active_keys)
    if targeted:
        obsolete = obsolete.filter(pk__in=old_page_ids)
    obsolete.update(status="archived")
    lint = ({"issues": sum(lint_page(p) for p in KnowledgePage.objects.filter(key__in=active_keys))}
            if targeted else lint_all())
    obsidian = {"enabled": False}
    if not targeted and getattr(settings, "OBSIDIAN_EXPORT_ENABLED", True):
        from .obsidian import export_vault

        obsidian = {"enabled": True, **export_vault()}
    return {
        "pages": len(specs),
        "revisions_created": changed,
        "by_type": dict(counts),
        "lint": lint,
        "generation_mode": "hybrid" if llm_used else "template",
        "llm_revisions": llm_revisions,
        "template_revisions": template_revisions,
        "failed_pages": failed_pages,
        "obsidian": obsidian,
        "relation_audit": relation_audit,
        "input_hash": input_hash,
    }
