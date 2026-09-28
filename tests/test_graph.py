import pytest
from analysis.graph import build_graph
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command


def fixture_graph(result, authorize=lambda *args: None):
    return build_graph(
        InMemorySaver(),
        authorize,
        lambda *args: ["e1"],
        lambda ids: {"e1": "供水设施进行数字化改造。"},
        lambda *args: result,
    )


def test_graph_requires_review_and_resumes():
    graph = fixture_graph(
        {"claims": [{"text": "涉及供水", "evidence_id": "e1", "quote": "供水设施"}]}
    )
    config = {"configurable": {"thread_id": "test-run"}}
    result = graph.invoke(
        {"actor_id": 1, "policy_id": "p1", "input_version": 1, "question": "是否涉及供水？"}, config
    )
    assert result["status"] == "needs_review"
    assert result["__interrupt__"]
    assert graph.invoke(Command(resume=True), config)["status"] == "reviewed"


def test_hallucinated_citation_is_not_exposed():
    graph = fixture_graph(
        {"claims": [{"text": "补助一百万", "evidence_id": "e1", "quote": "一百万"}]}
    )
    result = graph.invoke(
        {"actor_id": 1, "policy_id": "p1", "input_version": 1, "question": "补助多少？"},
        {"configurable": {"thread_id": "bad"}},
    )
    assert result["status"] == "invalid_citation"
    assert result["result"]["claims"] == []


def test_revoked_access_blocks_resume():
    permitted = [True]

    def authorize(*args):
        if not permitted[0]:
            raise PermissionError("revoked")

    graph = fixture_graph(
        {"claims": [{"text": "涉及供水", "evidence_id": "e1", "quote": "供水设施"}]}, authorize
    )
    config = {"configurable": {"thread_id": "revoked"}}
    graph.invoke(
        {"actor_id": 1, "policy_id": "p1", "input_version": 1, "question": "供水？"}, config
    )
    permitted[0] = False
    with pytest.raises(PermissionError):
        graph.invoke(Command(resume=True), config)
