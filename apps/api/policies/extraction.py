import re


def extract_metadata(body):
    """Stored extractive summary and anchored terms; no model inference or fact completion."""
    keywords = []
    for category, terms in {
        "industry": ["水务", "水利", "供水", "污水", "环保", "生态环境", "人工智能", "大模型"],
        "support": [
            "财政资金",
            "补助",
            "奖励",
            "税费",
            "融资",
            "贷款",
            "试点",
            "示范",
            "认定",
            "资质",
            "目录",
            "推广",
        ],
        "action": ["申报", "延期", "公示", "废止", "修订", "征求意见", "验收"],
    }.items():
        for term in terms:
            offset = body.find(term)
            if offset >= 0:
                keywords.append(
                    {"term": term, "category": category, "start": offset, "end": offset + len(term)}
                )
    # Literal excerpt: show its origin rather than presenting an AI-written conclusion.
    summary = body[:500].strip()
    for number in re.findall(r"[^\s]{0,15}〔\d{4}〕\d+号", body)[:10]:
        offset = body.find(number)
        keywords.append(
            {
                "term": number,
                "category": "document_number",
                "start": offset,
                "end": offset + len(number),
            }
        )
    return {"summary": summary, "structured_keywords": keywords}
