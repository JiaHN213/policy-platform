"""LLM-maintained knowledge pages with a strict citation boundary."""

from dataclasses import dataclass
from typing import TypedDict

import httpx
from core.ai_runtime import get_ai_profile
from langgraph.graph import END, START, StateGraph
from policies.enrichment import model_json
from policies.grounding import unsupported_numbers
from pydantic import BaseModel, ConfigDict, Field

PROMPT_VERSION = "wiki-llm-grounded-v3"


def synthesis_failure(exc):
    if isinstance(exc, httpx.TimeoutException):
        return "模型服务响应超时，请稍后重试。"
    if isinstance(exc, httpx.HTTPStatusError):
        return "模型服务拒绝了请求，请检查连接、额度和服务状态。"
    if isinstance(exc, httpx.RequestError):
        return "无法连接模型服务，请检查网络和模型地址。"
    return {
        "WIKI_INVALID_CITATION_REFERENCE": "模型引用了资料列表中不存在的编号。",
        "WIKI_UNSUPPORTED_NUMERIC_CLAIM": "生成内容中的数字或比例无法在其引用的原文中核对。",
        "MODEL_INVALID_OUTPUT": "模型连续返回不完整或不符合格式要求的内容。",
        "WIKI_NO_CITATIONS": "当前知识页缺少可用于综合的原文证据。",
    }.get(str(exc), "知识页生成发生处理异常，请检查服务日志后重试。")


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WikiParagraph(StrictOutput):
    text: str = Field(min_length=1, max_length=1200)
    citation_ids: list[int] = Field(min_length=1, max_length=6)


class WikiSection(StrictOutput):
    heading: str = Field(min_length=1, max_length=100)
    paragraphs: list[WikiParagraph] = Field(min_length=1, max_length=6)


class WikiDocument(StrictOutput):
    abstract: str = Field(min_length=1, max_length=300)
    sections: list[WikiSection] = Field(min_length=2, max_length=8)


class SynthesisState(TypedDict, total=False):
    payload: dict
    output: WikiDocument
    body: str


@dataclass(frozen=True)
class SynthesisResult:
    body: str
    abstract: str
    model: str
    prompt_version: str


def enabled():
    from django.conf import settings

    return getattr(settings, "KNOWLEDGE_LLM_ENABLED", True) and get_ai_profile(
        "wiki_synthesis"
    ).configured


def _payload(spec, previous_body):
    # Large topic pages retain a deterministic complete appendix. The model receives a
    # bounded representative context so one page cannot exhaust the worker or model.
    citations = [
        {
            "id": index,
            "title": citation["title"],
            "document_number": citation.get("document_number", ""),
            "quote": citation["quote"],
            "purpose": citation.get("purpose", "政策依据"),
        }
        for index, citation in enumerate(spec.citations[:30], start=1)
    ]
    draft = spec.body
    if len(spec.citations) > len(citations):
        cutoff = draft.find(f"[{len(citations) + 1}]")
        if cutoff > 0:
            draft = draft[:cutoff]
    return {
        "page_type": spec.page_type,
        "title": spec.title,
        "current_structured_draft": draft[:24000],
        "previous_published_page": (previous_body or "")[:8000],
        "citations": citations,
        "total_source_count": len(spec.policies),
    }


def _render(document, citation_count, citations=None):
    def citation_material(item):
        return "\n".join(item.get(key, "") for key in ("title", "document_number", "quote"))

    lines = []
    for section in document.sections:
        lines += [f"## {section.heading}", ""]
        for paragraph in section.paragraphs:
            ids = list(dict.fromkeys(paragraph.citation_ids))
            if not ids or any(value < 1 or value > citation_count for value in ids):
                raise ValueError("WIKI_INVALID_CITATION_REFERENCE")
            if citations is not None:
                evidence = "\n".join(citation_material(citations[value - 1]) for value in ids)
                if unsupported_numbers(paragraph.text, evidence):
                    raise ValueError("WIKI_UNSUPPORTED_NUMERIC_CLAIM")
            markers = "".join(f"[{value}]" for value in ids)
            lines += [f"{paragraph.text.strip()} {markers}", ""]
    if citations is not None and unsupported_numbers(
        document.abstract, "\n".join(citation_material(item) for item in citations)
    ):
        raise ValueError("WIKI_UNSUPPORTED_NUMERIC_CLAIM")
    return "\n".join(lines).strip()


def _complete_source_appendix(spec):
    if spec.page_type not in {"topic", "region"}:
        return ""
    lines = ["## 完整收录清单", ""]
    for policy in sorted(
        spec.policies, key=lambda item: (item.publication_date, str(item.pk)), reverse=True
    ):
        lines.append(
            f"- {policy.publication_date.isoformat()} "
            f"[{policy.title}]({policy.source_url})"
        )
    return "\n".join(lines)


def synthesize(spec, previous_body=""):
    """Use LangGraph and the configured model to rewrite one changed knowledge page."""
    payload = _payload(spec, previous_body)
    if not payload["citations"]:
        raise ValueError("WIKI_NO_CITATIONS")

    def generate(state):
        citation_count = len(state["payload"]["citations"])
        output = model_json(
            "你负责持续维护政策知识页。根据当前结构化草稿和上一版页面，生成易读、简洁、可持续更新的知识页。"
            "只允许使用本次提供的资料，不得从上一版继承本次资料不再支持的事实。"
            f"每个事实段落必须填写一个或多个citation_ids，编号只能是1到{citation_count}之间的整数。"
            "引用只表示证据来源，不得把主题相似描述成实施、替代、废止等正式关系。"
            "若资料存在不确定或冲突，应明确说明，禁止自行消除冲突。"
            "金额、比例、日期必须在该段引用的原文中有依据，保留原单位，不换算或推算。"
            "不要输出一级标题、引用清单、Markdown链接或原文入口，系统会统一补充。",
            state["payload"],
            WikiDocument,
            max_tokens=3600,
            purpose="wiki_synthesis",
        )
        return {**state, "output": output}

    def validate(state):
        citations = state["payload"]["citations"]
        body = _render(state["output"], len(citations), citations)
        return {**state, "body": body}

    graph = StateGraph(SynthesisState)
    graph.add_node("synthesize", generate)
    graph.add_node("validate", validate)
    graph.add_edge(START, "synthesize")
    graph.add_edge("synthesize", "validate")
    graph.add_edge("validate", END)
    compiled = graph.compile()
    state = None
    for attempt in range(2):
        try:
            state = compiled.invoke({"payload": payload})
            break
        except ValueError as exc:
            if str(exc) not in {"WIKI_INVALID_CITATION_REFERENCE", "WIKI_UNSUPPORTED_NUMERIC_CLAIM"} or attempt:
                raise
            payload = {
                **payload,
                "correction": (
                    "上一次输出引用编号无效，或数字、比例在该段引用中没有依据。删除无依据结论，保留原单位，citation_ids只能使用"
                    f"1到{len(payload['citations'])}。"
                ),
            }
    if state is None:
        raise ValueError("WIKI_SYNTHESIS_FAILED")

    body = f"# {spec.title}\n\n{state['body']}"
    appendix = _complete_source_appendix(spec)
    if appendix:
        body += "\n\n" + appendix
    return SynthesisResult(
        body=body,
        abstract=state["output"].abstract.strip(),
        model=get_ai_profile("wiki_synthesis").model,
        prompt_version=PROMPT_VERSION,
    )


def template_result(spec):
    """Deterministic safe mode for installations without a configured model."""
    return SynthesisResult(
        body=spec.body,
        abstract=spec.abstract,
        model="",
        prompt_version="wiki-template-v1",
    )
