import json

from agentproof.autofix import autofix
from agentproof.graph import NodeType
from agentproof.importers import (
    detect_format,
    import_dify,
    import_generic_json,
    import_n8n,
    import_openai_builder,
)
from agentproof.simulator import run_suite


def _n8n_workflow():
    return {
        "name": "Refund flow",
        "nodes": [
            {"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
            {"name": "Lookup Customer", "type": "n8n-nodes-base.postgres"},
            {"name": "Refund", "type": "n8n-nodes-base.stripe"},
            {"name": "Send Email", "type": "n8n-nodes-base.gmail"},
        ],
        "connections": {
            "Start": {"main": [[{"node": "Lookup Customer", "type": "main", "index": 0}]]},
            "Lookup Customer": {"main": [[{"node": "Refund"}]]},
            "Refund": {"main": [[{"node": "Send Email"}]]},
        },
    }


def test_detect_n8n():
    assert detect_format(_n8n_workflow()) == "n8n"


def test_import_n8n_edges_from_connections():
    graph = import_n8n(_n8n_workflow())
    assert graph.node("Send Email").config.get("external") is True
    assert graph.node("Start").type == NodeType.INPUT
    edge_keys = {(e.source, e.target) for e in graph.edges}
    assert ("Start", "Lookup Customer") in edge_keys
    assert ("Refund", "Send Email") in edge_keys


def _dify_app():
    return {
        "app": {"name": "Support bot"},
        "workflow": {
            "graph": {
                "nodes": [
                    {"id": "start", "data": {"type": "start", "title": "Start"}},
                    {"id": "kb", "data": {"type": "knowledge-retrieval", "title": "Lookup"}},
                    {"id": "llm", "data": {"type": "llm", "title": "Answer"}},
                    {"id": "http", "data": {"type": "http-request", "title": "Send email"}},
                    {"id": "end", "data": {"type": "end", "title": "End"}},
                ],
                "edges": [
                    {"source": "start", "target": "kb"},
                    {"source": "kb", "target": "llm"},
                    {"source": "llm", "target": "http"},
                    {"source": "http", "target": "end"},
                ],
            }
        },
    }


def test_detect_and_import_dify():
    data = _dify_app()
    assert detect_format(data) == "dify"
    graph = import_dify(data)
    assert graph.name == "Support bot"
    assert graph.node("start").type == NodeType.INPUT
    assert graph.node("llm").type == NodeType.LLM
    assert graph.node("http").config.get("external") is True
    assert graph.node("kb").config.get("returns_pii") is True
    assert graph.node("end").type == NodeType.OUTPUT


def _openai_builder_workflow():
    return {
        "name": "Refund agent",
        "nodes": [
            {"id": "n1", "type": "start", "data": {"label": "Start"}},
            {"id": "n2", "type": "guardrail", "data": {"label": "Safety guardrail"}},
            {"id": "n3", "type": "agent", "data": {"label": "Refund agent"}},
            {"id": "n4", "type": "tool", "data": {"label": "process_refund"}},
            {"id": "n5", "type": "end", "data": {"label": "End"}},
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
            {"source": "n3", "target": "n4"},
            {"source": "n4", "target": "n5"},
        ],
    }


def test_detect_and_import_openai_builder():
    data = _openai_builder_workflow()
    assert detect_format(data) == "openai_builder"
    graph = import_openai_builder(data)
    assert graph.node("n1").type == NodeType.INPUT
    assert graph.node("n2").type == NodeType.GUARD
    assert graph.node("n3").type == NodeType.LLM
    assert graph.node("n5").type == NodeType.OUTPUT


def test_generic_json_dispatches_by_shape():
    assert import_n8n(_n8n_workflow()).name == import_generic_json(_n8n_workflow()).name
    assert import_generic_json(_dify_app()).node("llm").type == NodeType.LLM
    assert import_generic_json(_openai_builder_workflow()).node("n2").type == NodeType.GUARD


def test_imported_n8n_agent_is_verifiable_and_fixable(spec, scenarios):
    graph = import_n8n(_n8n_workflow())
    # rename the Refund node config to spend so it enforces limits
    graph.node("Refund").config["spend"] = True
    graph.node("Refund").config.pop("external", None)
    results = run_suite(graph, spec, scenarios)
    assert any(not r.passed for r in results)
    repaired = autofix(graph, spec, results).graph
    # autofix should have added at least guards/gates
    assert repaired.nodes_of_type(NodeType.GUARD) or repaired.nodes_of_type(NodeType.CONDITION)


def test_native_format_still_roundtrips(naive_graph):
    data = naive_graph.to_dict()
    assert detect_format(data) == "native"
    graph = import_generic_json(data)
    assert {n.id for n in graph.nodes} == {n.id for n in naive_graph.nodes}
