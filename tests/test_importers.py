import json
from pathlib import Path

from agentproof.autofix import autofix
from agentproof.graph import NodeType
from agentproof.importers import import_agent, import_flowise, import_langgraph
from agentproof.simulator import run_suite

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_import_langgraph_example():
    graph = import_agent(EXAMPLES / "existing_langgraph_agent.py")
    ids = {n.id for n in graph.nodes}
    assert {"input", "planner", "lookup_customer", "process_refund", "send_email", "output"} <= ids
    assert graph.node("process_refund").config.get("spend") is True
    assert graph.node("lookup_customer").config.get("returns_pii") is True
    assert graph.node("send_email").config.get("external") is True
    assert graph.node("planner").type == NodeType.LLM


def test_imported_agent_fails_simulation_then_autofix_repairs_it(spec, scenarios):
    graph = import_agent(EXAMPLES / "existing_langgraph_agent.py")
    results = run_suite(graph, spec, scenarios)
    assert any(not r.passed for r in results), "an unguarded imported agent must fail"
    report = autofix(graph, spec, results)
    repaired = run_suite(report.graph, spec, scenarios)
    assert all(r.passed for r in repaired)


def test_import_flowise_json():
    data = {
        "name": "Support flow",
        "nodes": [
            {"id": "start_0", "data": {"label": "Start", "name": "startAgentflow"}},
            {"id": "llm_0", "data": {"label": "Support LLM", "name": "chatOpenAI"}},
            {"id": "refund_0", "data": {"label": "Refund tool", "name": "customTool"}},
            {"id": "email_0", "data": {"label": "Send email", "name": "gmailTool"}},
            {"id": "end_0", "data": {"label": "End", "name": "endAgentflow"}},
        ],
        "edges": [
            {"source": "start_0", "target": "llm_0"},
            {"source": "llm_0", "target": "refund_0"},
            {"source": "refund_0", "target": "llm_0"},
            {"source": "llm_0", "target": "email_0"},
            {"source": "email_0", "target": "end_0"},
        ],
    }
    graph = import_flowise(data)
    assert graph.node("start_0").type == NodeType.INPUT
    assert graph.node("llm_0").type == NodeType.LLM
    assert graph.node("refund_0").config.get("spend") is True
    assert graph.node("email_0").config.get("external") is True
    assert graph.node("end_0").type == NodeType.OUTPUT
    assert len(graph.edges) == 5


def test_import_agentproof_json_roundtrip(tmp_path, naive_graph):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(naive_graph.to_dict()))
    graph = import_agent(path)
    assert {n.id for n in graph.nodes} == {n.id for n in naive_graph.nodes}


def test_import_langgraph_conditional_edges():
    source = """
from langgraph.graph import END, START, StateGraph
g = StateGraph(dict)
g.add_node("router_llm", lambda s: s)
g.add_node("refund_tool", lambda s: s)
g.add_node("responder_llm", lambda s: s)
g.add_edge(START, "router_llm")
g.add_conditional_edges("router_llm", lambda s: "a", {"refund": "refund_tool", "answer": "responder_llm"})
g.add_edge("refund_tool", "responder_llm")
g.add_edge("responder_llm", END)
"""
    graph = import_langgraph(source)
    labels = {e.label for e in graph.edges}
    assert "refund" in labels and "answer" in labels
    assert graph.has_node("input") and graph.has_node("output")
