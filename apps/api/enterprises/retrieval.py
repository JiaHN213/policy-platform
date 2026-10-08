"""Multi-route recall with database visibility and explicit filters as authority."""
from policies.business_scope import configured_domains, configured_tags
from policies.opensearch import OpenSearchUnavailable, configured, search_policy_ids
from policies.search_text import search_terms


def recall_terms(target):
    terms = []
    for field, options in [("business_domains", configured_domains()), ("direction_tags", configured_tags())]:
        for code in target.get(field, []):
            if code in options:
                label, synonyms = options[code]
                terms.extend([label, *synonyms])
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))[:40]


def index_candidates(user, terms):
    if not terms or not configured():
        return set(), "database"
    try:
        result = search_policy_ids(" ".join(terms[:8]), {"scope": "all", "sort": "relevance"},
                                   include_demo=user.is_staff, page=1, page_size=100,
                                   terms=terms[:8], require_all_terms=False)
        return set(result.ids), "opensearch_and_database"
    except OpenSearchUnavailable:
        return set(), "database_fallback"


def filter_policy(policy, filters):
    for field in ("province", "city", "region", "validity_status", "source_grade", "document_type", "geographic_level", "industry"):
        if filters.get(field) and getattr(policy, field) != filters[field]:
            return False
    for field, collection in [("business_domain", "business_domains"), ("direction_tag", "direction_tags")]:
        if filters.get(field) and filters[field] not in getattr(policy, collection):
            return False
    if filters.get("topic") and filters["topic"] not in policy.topics:
        return False
    date = policy.publication_date.isoformat()
    if (filters.get("published_from") and date < filters["published_from"]) or (filters.get("published_to") and date > filters["published_to"]):
        return False
    scope = filters.get("scope", "all")
    text = getattr(policy, scope) if scope in {"title", "document_number", "issuer"} else "\n".join([policy.title, policy.document_number, policy.issuer, policy.summary, policy.body])
    return all(term.casefold() in text.casefold() for term in search_terms(filters.get("q", "")))
