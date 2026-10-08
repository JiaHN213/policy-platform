"""Compare only explicit, grounded conditions against confirmed facts.

Unrecognised language, absent facts and compound alternatives remain unknown.
"""
import re
from decimal import Decimal

from policies.catalog import batch_state
from policies.enrichment import grounded_quote

from .policy_evidence import condition_source

LABELS = {"consistent": "已核对条件相符", "conflict": "存在明确冲突", "unknown": "信息不足，需核对"}


def compare_condition(text, profile, project=None):
    text = text.strip().rstrip("。；;")
    money = re.fullmatch(r"项目总投资(?:须|必须|应)?(不低于|不少于|不超过|高于|低于)(\d+(?:\.\d+)?)万元", text)
    revenue = re.fullmatch(r"(?:企业)?((?:19|20)\d{2})年营业收入(?:须|必须|应)?(不低于|不少于|不超过|高于|低于)(\d+(?:\.\d+)?)万元", text)
    if money or revenue:
        field = "project.investment_wan" if money else "annual_revenue_wan"
        value = (project or {}).get("investment_wan") if money else profile.get("annual_revenue_wan")
        if revenue and profile.get("annual_revenue_year") != revenue[1]:
            return "unknown", f"请补充{revenue[1]}年度营业收入及对应年度，其他年份不能替代。", "annual_revenue_year"
        if value is None or not re.fullmatch(r"\d+(?:\.\d+)?", str(value)):
            return "unknown", "请补充已确认金额，统一以万元填写。", field
        operator, expected = (money[1], money[2]) if money else (revenue[2], revenue[3])
        actual, expected = Decimal(str(value)), Decimal(expected)
        passed = {"不低于": actual >= expected, "不少于": actual >= expected, "不超过": actual <= expected,
                  "高于": actual > expected, "低于": actual < expected}[operator]
        return ("consistent" if passed else "conflict"), f"要求{operator}{expected}万元，已确认金额{actual}万元。", field
    subject = re.fullmatch(r"申报主体(?:必须为|须为|应为|为)([^。；;，,或且]{2,30})", text)
    stage = re.fullmatch(r"项目(?:阶段|状态)(?:必须为|须为|应为|为)(拟建|在建|已完工|已投产)", text)
    if subject or stage:
        field = "subject_type" if subject else "project.project_stage"
        expected = subject[1] if subject else stage[1]
        actual = profile.get("subject_type") if subject else (project or {}).get("project_stage")
        if not actual:
            return "unknown", "请确认申报主体类型或项目当前阶段。", field
        # Only compare known literal categories; synonyms and compound legal forms need review.
        choices = {"企业", "事业单位", "社会组织", "个人"} if subject else {"拟建", "在建", "已完工", "已投产"}
        if expected not in choices or actual not in choices:
            return "unknown", "类型表述尚不能直接对应，请结合原文核对。", field
        return ("consistent" if actual == expected else "conflict"), f"原文要求{expected}；已确认信息为{actual}。", field
    negative = re.fullmatch(r"企业不得(?:具备|具有)([^。；;，,或且]{2,60})", text)
    if negative:
        facts, expected = profile.get("capabilities", []), negative[1]
        if expected in facts:
            return "conflict", "已确认能力与原文明确排除条件冲突。", "capabilities"
        if "不具备" + expected in facts or "未取得" + expected in facts:
            return "consistent", "企业明确确认不具备该被排除条件。", "capabilities"
        return "unknown", "未填写不能视为不存在排除情形，请核对。", "capabilities"
    if re.search(r"或|之一|除外|以及|且|同时|不低于|以上|以下|优先|鼓励", text):
        return "unknown", "复杂或可选条件需结合完整条款核对。", ""
    location = re.fullmatch(r"(?:企业)?注册地(?:必须为|须为|应为|为|在)([\u4e00-\u9fff]{2,12}市)[。；;]?", text)
    if location:
        actual = profile.get("registered_city", "")
        if not actual:
            return "unknown", "请补充企业注册城市；所在城市不能代替注册地。", "registered_city"
        equal = actual.removesuffix("市") == location[1].removesuffix("市")
        return ("consistent" if equal else "conflict"), f"要求注册地为{location[1]}；企业确认的注册城市为{actual}。", "registered_city"
    location = re.fullmatch(r"项目(?:实施地|建设地)(?:必须为|须为|应为|为|在)([\u4e00-\u9fff]{2,12}市)[。；;]?", text)
    if location:
        actual = (project or {}).get("city", "")
        if not actual:
            return "unknown", "请确认项目实施城市。", "project.city"
        equal = actual.removesuffix("市") == location[1].removesuffix("市")
        return ("consistent" if equal else "conflict"), f"要求项目位于{location[1]}；已确认项目城市为{actual}。", "project.city"
    capability = re.fullmatch(r"(?:企业)?(?:须|必须|应)(?:具备|取得|具有)([^。；;，,]{2,60})[。；;]?", text)
    if capability:
        required = capability[1]
        facts = profile.get("capabilities", [])
        if required in facts:
            return "consistent", "与企业确认的能力线索一致；证书真实性、范围和有效期仍需核实。", "capabilities"
        if any(value in facts for value in ["未取得" + required, "不具备" + required]):
            return "conflict", "企业明确填写尚未具备该条件。", "capabilities"
        return "unknown", "请补充已确认的能力或资质线索；未填写不代表未取得。", "capabilities"
    return "unknown", "尚无可直接比较的已确认信息，需查看原文核对。", ""


def assess_conditions(policy, opportunities, profile, project, readiness, batch_map=None):
    groups = []
    for opportunity in opportunities:
        checks = []
        for kind in ("requirements", "prerequisites", "exclusion_conditions"):
            for raw in getattr(opportunity, kind):
                text = str(raw).strip()
                quote = grounded_quote(text, policy.body) if opportunity.evidence_version == policy.version and opportunity.evidence_policy_id == policy.pk else None
                state, reason, field = ("unknown", "条件尚无可定位的当前版本原文依据。", "")
                source = condition_source(policy, quote) if quote else None
                if quote:
                    if not source or not source["complete_clause"]:
                        reason = "引用不是完整独立条件，需结合前后例外、任选或限定条款核对。"
                    elif kind == "exclusion_conditions" and not text.startswith("企业不得"):
                        reason = "排除条款需单独核对，不能按正向条件判断。"
                    else:
                        state, reason, field = compare_condition(text, profile, project)
                checks.append({"condition": text, "status": state, "label": LABELS[state], "reason": reason, "quote": quote or "", "profile_field": field,
                               "source": source, "kind": kind})
        # Subjects, region lists and unparsed files cannot be converted into a full eligibility claim.
        gaps = []
        if readiness["status"] != "available":
            gaps.append(readiness["reason"])
        if opportunity.missing_information:
            gaps.append("机会信息存在缺口：" + "；".join(map(str, opportunity.missing_information)))
        if opportunity.eligible_subjects or opportunity.eligible_projects or opportunity.regions:
            gaps.append("适用对象、项目类型及适用地域尚需结合完整条款核对。")
        if opportunity.status not in {"open", "ongoing"}:
            gaps.append("当前尚不能确认已进入受理阶段。")
        batches = (batch_map or {}).get(opportunity.pk, [])
        if batches and not any(batch_state(batch)[0] in {"open", "ongoing"} for batch in batches):
            gaps.append("尚无已确认正在受理的申报批次。")
        state = "conflict" if any(c["status"] == "conflict" for c in checks) else (
            "consistent" if checks and all(c["status"] == "consistent" for c in checks) and not gaps else "unknown"
        )
        groups.append({"opportunity_id": str(opportunity.pk), "title": opportunity.title, "status": state,
                       "label": LABELS[state], "checks": checks, "gaps": gaps})
    # Different opportunities are alternatives: one conflict must not reject all others.
    state = "consistent" if any(g["status"] == "consistent" for g in groups) else (
        "conflict" if groups and all(g["status"] == "conflict" for g in groups) else "unknown"
    )
    if policy.validity_status in {"unverified", "not_effective"} and state == "consistent":
        state = "unknown"
    return {"status": state, "label": LABELS[state], "opportunities": groups,
            "notice": "仅比较已提取条件与企业确认信息；相符不等于资格认证，未填写不等于不满足。"}
