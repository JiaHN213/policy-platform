import re
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

from django.db import transaction
from django.db.models import Q, QuerySet

from .models import Policy, PolicySource


def _normalized(value):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", (value or "").casefold())


def _single(candidates: QuerySet):
    matches = list(candidates.order_by("created_at", "id")[:2])
    return matches[0] if len(matches) == 1 else None


def find_canonical_policy(record, content_hash):
    """Return a canonical policy only when duplicate evidence is deterministic."""
    exact = Policy.objects.filter(content_hash=content_hash).order_by("created_at", "id").first()
    if exact:
        return exact, "content_hash", Decimal("1.000"), {"content_hash": content_hash}

    document_number = _normalized(record.get("document_number"))
    title = _normalized(record.get("title"))
    if document_number and title:
        candidates = Policy.objects.exclude(document_number="")
        matches = [
            policy
            for policy in candidates.only("id", "document_number", "title", "created_at")
            if _normalized(policy.document_number) == document_number
            and _normalized(policy.title) == title
        ]
        if len(matches) == 1:
            return matches[0], "document_number_title", Decimal("0.990"), {
                "document_number": record.get("document_number", ""),
                "title": record.get("title", ""),
            }

    if len(title) >= 8 and record.get("publication_date") and record.get("issuer"):
        candidates = Policy.objects.filter(
            publication_date=record["publication_date"], issuer=record["issuer"][:200]
        )
        matches = [policy for policy in candidates if _normalized(policy.title) == title]
        if len(matches) == 1:
            return matches[0], "title_date_issuer", Decimal("0.970"), {
                "title": record.get("title", ""),
                "publication_date": str(record["publication_date"]),
                "issuer": record.get("issuer", ""),
            }
    return None, "new_policy", Decimal("1.000"), {}


def source_location(url):
    """Keep query identity; only normalize host case and fragment anchors."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ""))


@transaction.atomic
def attach_policy_source(
    policy,
    record,
    content_hash,
    *,
    resolved_url="",
    created_policy=False,
    match_method="new_policy",
    confidence=Decimal("1.000"),
    evidence=None,
):
    Policy.objects.select_for_update().get(pk=policy.pk)
    locations = [url for url in (record["url"], resolved_url) if url]
    existing = PolicySource.objects.filter(
        Q(url__in=locations) | Q(resolved_url__in=locations)
    ).order_by("-is_primary", "created_at", "id").first()
    if existing:
        if existing.policy_id != policy.pk:
            raise ValueError("SOURCE_URL_ALREADY_BOUND_TO_ANOTHER_POLICY")
        return existing
    role = PolicySource.Role.PRIMARY if created_policy else PolicySource.Role.REPOST
    grade = policy.source_grade if created_policy else Policy.SourceGrade.L2
    source, created = PolicySource.objects.get_or_create(
        url=record["url"],
        defaults={
            "policy": policy,
            "resolved_url": resolved_url,
            "publisher": record.get("publisher") or record.get("issuer", ""),
            "publication_date": record.get("publication_date"),
            "source_grade": grade,
            "role": role,
            "is_primary": created_policy,
            "content_hash": content_hash,
            "match_method": match_method,
            "match_confidence": confidence,
            "match_evidence": evidence or {},
        },
    )
    if not created and source.policy_id != policy.pk:
        raise ValueError("SOURCE_URL_ALREADY_BOUND_TO_ANOTHER_POLICY")
    return source
