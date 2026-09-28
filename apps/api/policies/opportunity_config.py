"""Compact the versioned opportunity dictionary for a small local model."""

from core.business_config import get_config


def _relevant_terms(terms, text, *, baseline=5, maximum=18):
    matched = [term for term in terms if term and term in text]
    return list(dict.fromkeys(matched + list(terms)[:baseline]))[:maximum]


def opportunity_context(text):
    dictionary = get_config("opportunity_identification")
    categories = {
        label: _relevant_terms(terms, text, baseline=6, maximum=20)
        for label, terms in dictionary.get("opportunity_type_keywords", {}).items()
    }
    acquisition = {
        code: _relevant_terms(terms, text, baseline=3, maximum=10)
        for code, terms in dictionary.get("acquisition_action_keywords", {}).items()
    }
    roles = {
        code: _relevant_terms(terms, text, baseline=3, maximum=12)
        for code, terms in dictionary.get("document_role_keywords", {}).items()
    }
    return {
        "version": dictionary.get("meta", {}).get("version", "unversioned"),
        "core_rule": dictionary.get("meta", {}).get("core_rule", ""),
        "opportunity_types": categories,
        "acquisition_methods": acquisition,
        "document_roles": roles,
        "benefit_actions": _relevant_terms(
            dictionary.get("benefit_action_keywords", []), text, baseline=8, maximum=24
        ),
        "weak_signals": _relevant_terms(
            dictionary.get("weak_signal_keywords", []), text, baseline=8, maximum=20
        ),
        "result_signals": _relevant_terms(
            dictionary.get("result_keywords", []), text, baseline=8, maximum=24
        ),
        "negative_context": {
            code: _relevant_terms(terms, text, baseline=3, maximum=10)
            for code, terms in dictionary.get("negative_context_keywords", {}).items()
        },
        "current_or_historical": dictionary.get("context_keywords", {}),
        "application_channels": _relevant_terms(
            dictionary.get("application_channel_keywords", []), text, baseline=6, maximum=16
        ),
    }


def opportunity_material(body, *, limit=18000):
    """Cover opportunity-bearing sections across a long body and parsed attachments."""
    if len(body) <= limit:
        return body
    dictionary = get_config("opportunity_identification")
    terms = []
    for values in dictionary.get("opportunity_type_keywords", {}).values():
        terms.extend(values)
    for values in dictionary.get("acquisition_action_keywords", {}).values():
        terms.extend(values)
    for key in (
        "benefit_action_keywords",
        "result_keywords",
        "attachment_keywords",
        "application_channel_keywords",
    ):
        terms.extend(dictionary.get(key, []))
    ranges = [(0, 3500), (max(0, len(body) - 3500), len(body))]
    for term in sorted(set(terms), key=len, reverse=True):
        start = 0
        while (position := body.find(term, start)) >= 0:
            ranges.append((max(0, position - 650), min(len(body), position + len(term) + 950)))
            start = position + len(term)
            if len(ranges) >= 80:
                break
        if len(ranges) >= 80:
            break
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 100:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    pieces, used = [], 0
    for start, end in merged:
        remaining = limit - used
        if remaining <= 0:
            break
        piece = body[start:end][:remaining]
        pieces.append(piece)
        used += len(piece)
    return "\n【同一政策的另一处相关摘录】\n".join(pieces)
