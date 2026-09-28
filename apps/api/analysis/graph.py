"""LangGraph with dependency-injected read tools and model; no publication tools."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field


class Claim(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    evidence_id: str
    quote: str = Field(min_length=1, max_length=2000)


class AnalysisOutput(BaseModel):
    claims: list[Claim] = Field(max_length=20)
    gaps: list[str] = Field(default_factory=list, max_length=20)


class GraphState(TypedDict, total=False):
    actor_id: int
    policy_id: str
    input_version: int
    question: str
    evidence_ids: list[str]
    result: dict
    status: str


def build_graph(checkpointer, authorize, retrieve, read_evidence, generate):
    def check_access(state):
        authorize(state["actor_id"], state["policy_id"], state["input_version"])
        return {"status": "running"}

    def collect(state):
        check_access(state)
        return {"evidence_ids": retrieve(state["policy_id"], state["input_version"])}

    def produce(state):
        check_access(state)
        evidence = read_evidence(state["evidence_ids"])
        if not evidence:
            return {
                "result": {"claims": [], "gaps": ["未找到可核验的证据。"]},
                "status": "insufficient_evidence",
            }
        output = AnalysisOutput.model_validate(generate(state["question"], evidence))
        return {"result": output.model_dump()}

    def validate(state):
        check_access(state)
        evidence = read_evidence(state["evidence_ids"])
        for claim in state["result"]["claims"]:
            if (
                claim["evidence_id"] not in evidence
                or claim["quote"] not in evidence[claim["evidence_id"]]
            ):
                return {
                    "status": "invalid_citation",
                    "result": {"claims": [], "gaps": ["引用校验失败，未展示生成结论。"]},
                }
        return {"status": "needs_review" if state["result"]["claims"] else "insufficient_evidence"}

    def review(state):
        check_access(state)
        decision = interrupt({"kind": "analysis_review", "result": state["result"]})
        # Resume reexecutes the node, so revoked access/version changes are rechecked.
        check_access(state)
        return {"status": "reviewed" if decision is True else "rejected"}

    graph = StateGraph(GraphState)
    for name, fn in [
        ("authorize", check_access),
        ("retrieve", collect),
        ("generate", produce),
        ("validate", validate),
        ("review", review),
    ]:
        graph.add_node(name, fn)
    graph.add_edge(START, "authorize")
    graph.add_edge("authorize", "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges(
        "validate", lambda s: "review" if s["status"] == "needs_review" else END
    )
    graph.add_edge("review", END)
    return graph.compile(checkpointer=checkpointer)
