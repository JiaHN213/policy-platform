"""Shared search terms and readable, source-backed result previews."""

import re

SEARCH_FIELDS = {"title": 12, "document_number": 16, "issuer": 5, "summary": 4, "body": 1}


def search_terms(query):
    # Quoted document numbers/titles retain their spaces; punctuation is literal.
    return list(dict.fromkeys(
        match[0] or match[1]
        for match in re.findall(r'"([^"\n]+)"|([^\s"]+)', query.strip())
    ))


def search_previews(policies, terms):
    previews = {}
    for policy in policies:
        fields = {
            "标题": policy.title, "文号": policy.document_number, "发文机关": policy.issuer,
            "摘要": policy.summary, "正文": policy.body,
        }
        matched = [name for name, value in fields.items()
                   if any(term.lower() in (value or "").lower() for term in terms)]
        # Prefer actual body context to generated summaries when searching.
        text = policy.body if "正文" in matched else policy.summary or policy.body
        positions = [text.lower().find(term.lower()) for term in terms if term]
        position = min((pos for pos in positions if pos >= 0), default=0)
        start = max(0, position - 55)
        end = min(len(text), start + 240)
        previews[str(policy.pk)] = {
            "text": ("…" if start else "") + text[start:end] + ("…" if end < len(text) else ""),
            "source": "正文片段" if text == policy.body else "政策摘要",
            "matched_fields": matched,
        }
    return previews
