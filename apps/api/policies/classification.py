from core.business_config import get_config

from .models import Policy


def suggest_document_type(title):
    """Conservative title hints only; a reviewer must confirm the publication type."""
    kinds = Policy.DocumentType
    configured = get_config("document_classification").get("title_signals", {})
    signals = {
        kinds.INTERPRETATION: configured.get("interpretation", []),
        kinds.DRAFT: configured.get("draft", []),
        kinds.RESULT: configured.get("result", []),
        kinds.OPPORTUNITY: configured.get("opportunity", []),
        kinds.POLICY: configured.get("policy", []),
    }
    # Mixed signals intentionally stay unclassified instead of guessing from a quoted policy name.
    matches = [kind for kind, words in signals.items() if any(word in title for word in words)]
    if "征求" in title and "意见" in title and kinds.DRAFT not in matches:
        matches.append(kinds.DRAFT)
    if kinds.DRAFT in matches and kinds.POLICY in matches:
        matches.remove(kinds.POLICY)
    return matches[0] if len(matches) == 1 else kinds.UNCLASSIFIED
