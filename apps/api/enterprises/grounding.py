"""Conservative draft checks; uncertain facts are left for the user to supply."""
import re

from core.business_config import get_config

VALIDATION_VERSION = "enterprise-grounding-v4"
COMPANY_NAMES = re.compile(r"[\u4e00-\u9fffA-Za-z0-9（）()]{2,80}?(?:有限责任公司|有限公司|股份公司|集团公司|总公司)")
RELATED_SUBJECT = re.compile(r"母公司|子公司|控股股东|关联企业|招标方")
SENTENCES = re.compile(r"[^。！？!?；;\r\n]+[。！？!?；;]?")


def compact(value):
    return re.sub(r"\s+", "", str(value)).casefold()


def company_evidence_error(item, sources, name, inputs):
    """Check each quote's sentence, then the nearest explicit subject.

    An investor named in a previous sentence is not automatically the subject
    of the target's business sentence. Generic '公司' is not a legal name.
    """
    source = next((s for s in sources if s["id"] == item.source_id), {})
    text, quote = source.get("text", ""), item.quote
    occurrences = list(re.finditer(re.escape(quote), text)) if quote else []
    if not occurrences:
        return "引用未能在所选资料中定位"
    sentences = list(SENTENCES.finditer(text))
    for occurrence in occurrences:
        local = [s for s in sentences if s.start() < occurrence.end() and s.end() > occurrence.start()]
        context = "".join(s.group() for s in local)
        companies = COMPANY_NAMES.findall(context)
        if RELATED_SUBJECT.search(context):
            return "引用涉及母子公司、股东或关联主体，不能直接归入本企业"
        if any(name not in company for company in companies):
            return "引用所在句子涉及其他企业，尚不能确认属于本企业"
        if name in context:
            continue
        previous = [s for s in sentences if s.end() <= occurrence.start() and occurrence.start() - s.end() <= 1200]
        owner = None
        for sentence in reversed(previous):
            subjects = COMPANY_NAMES.findall(sentence.group())
            if not subjects and name not in sentence.group():
                continue
            if RELATED_SUBJECT.search(sentence.group()):
                owner = False
            elif sentence.group().strip().startswith(name) or sentence.group().strip().startswith("企业名称：" + name):
                # The introductory sentence may also list investors after the
                # target's name. Its grammatical lead remains the target.
                owner = True
            else:
                owner = name in sentence.group() and all(name in company for company in subjects)
            break
        if owner is False:
            return "相邻原文的主体是其他企业或存在关联主体歧义"
        if owner is True:
            continue
        # Private introductions/authorized website reads may say 'we'; a name
        # found elsewhere in a search page alone is not attribution evidence.
        if not (inputs.get("source_mode") in {"text", "file", "website"} and inputs.get("name") == name):
            return "引用未明确所属企业，需补充能确认主体的原文"
    return None


def belongs_to_company(item, sources, name, inputs):
    return company_evidence_error(item, sources, name, inputs) is None


def supported_value(field, value, evidence):
    quotes = [e["quote"] for e in evidence]
    if field == "annual_revenue_year":
        # A founding/project year is not evidence of a revenue reporting year.
        if not re.fullmatch(r"(?:19|20)\d{2}", value):
            return ""
        for quote in quotes:
            for sentence in SENTENCES.findall(quote):
                if (re.search(rf"(?<!\d){re.escape(value)}(?:年|年度)", sentence)
                        and re.search(r"营业(?:总)?收入|营收|销售收入", sentence)
                        and not re.search(r"目标|预计|预测|计划|力争", sentence)):
                    years = set(re.findall(r"(?<!\d)((?:19|20)\d{2})(?:年|年度)", sentence))
                    if years == {value}:
                        return value
        return ""
    if field in {"business_domains", "direction_tags"}:
        options = {item["code"]: item for item in get_config("business_scope")[field] if item.get("enabled", True)}
        accepted = []
        for code in value:
            option = options.get(code)
            if not option:
                continue
            terms = [option["label"], *option.get("terms", [])]
            # Negative/aspirational sentences are not evidence of current ability.
            positive = [q for q in quotes if not re.search(r"不涉及|未开展|尚未|不具备|计划开展|拟开展", q)]
            if any(compact(term) in compact(q) for term in terms for q in positive):
                accepted.append(code)
        return accepted
    if field == "website":
        return value  # URL validation is performed by the caller.
    if field == "registered_city":
        return value if value and any(re.search(r"注册(?:地|地址|城市).{0,10}" + re.escape(value.removesuffix("市")), q) for q in quotes) else ""
    if field in {"city", "province"}:
        return value if value and any(re.search(r"(?:位于|坐落于|地址|所在地|办公地|总部)[^，,。；;\n]{0,15}" + re.escape(value), q) for q in quotes) else ""
    if field == "business_summary":
        # Preserve qualifiers and negation; a shorter substring can reverse meaning.
        return value if value in quotes else "；".join(dict.fromkeys(quotes))[:2000]
    if field == "interest_regions":
        preferences = [q for q in quotes if re.search(r"(?:希望|需要|重点)?关注[^。！？;\n]{0,30}(?:政策|地区|区域)|政策关注|订阅偏好", q)]
        return [v for v in value if any(compact(v) in compact(q) for q in preferences)]
    if isinstance(value, list):
        return [v for v in value if any(compact(v) in compact(q) for q in quotes)
                and not any(re.search(r"尚未|未取得|不具备|已失效|拟申请", q) for q in quotes if compact(v) in compact(q))]
    if any(compact(value) in compact(q) for q in quotes) and value.strip():
        return value
    return ""
