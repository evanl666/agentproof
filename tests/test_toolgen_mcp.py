"""Rich tool scaffolds + the MCP catalog and bulk tool attach."""

import ast

import pytest

from agentproof.graph import Node, NodeType
from agentproof.mcp_catalog import catalog, server
from agentproof.spec import parse_spec
from agentproof.studio import DEFAULT_SPEC, StudioState
from agentproof.synthesis import synthesize
from agentproof.toolgen import render_tool_body, render_tools_module


def _graph_with_tools():
    spec = parse_spec(DEFAULT_SPEC)
    return synthesize(spec)


def test_rendered_tools_module_compiles_and_is_rich(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    g = _graph_with_tools()
    mod = render_tools_module(g)
    ast.parse(mod)  # valid Python
    # Rich scaffold, not a bare one-liner.
    assert "# TODO:" in mod
    assert "Reads from state:" in mod
    assert "with_retry" in mod


def test_money_tool_is_typed_and_raises():
    node = Node(id="process_refund", type=NodeType.TOOL, label="Refund", config={"spend": True})
    body = "\n".join(render_tool_body(node))
    ast.parse("def _wrap():\n" + "\n".join("    " + l for l in body.splitlines() if l) or "    pass")
    assert "amount: float" in body
    assert "NotImplementedError" in body
    assert "stripe" in body.lower() or "payment" in body.lower()


def test_unwired_money_stub_raises_notimplemented_not_typeerror():
    node = Node(id="pay", type=NodeType.TOOL, label="Pay", config={"spend": True})
    mod = "import time\nfrom functools import wraps\n" + "\n".join(render_tool_body(node))
    ns: dict = {}
    exec(compile(ast.parse(mod), "<tools>", "exec"), ns)
    with pytest.raises(NotImplementedError):
        ns["pay"]({})  # no 'amount' in state must still reach NotImplementedError


def test_mcp_catalog_shape():
    servers = catalog()
    assert {"github", "stripe", "postgres"} <= {s["id"] for s in servers}
    for s in servers:
        assert s["name"] and s["tools"]
        for t in s["tools"]:
            assert "label" in t and "risk" in t
    # Stripe's refund is money; GitHub's merge is high-risk.
    stripe_labels = {t["label"]: t["risk"] for t in server("stripe")["tools"]}
    assert stripe_labels["Issue refund"].get("money")
    gh = {t["label"]: t["risk"] for t in server("github")["tools"]}
    assert gh["Merge pull request"].get("high_risk")


def test_add_mcp_server_tools_carry_risk(tmp_path):
    state = StudioState(tmp_path)
    state.build(DEFAULT_SPEC)
    snap = state.add_tools(server("github")["tools"])
    assert len(snap["added_tools"]) == 5
    merge = next(n for n in state.graph.nodes_of_type(NodeType.TOOL) if "merge" in n.id)
    assert merge.config.get("high_risk") is True
    # It's wired into the loop and re-verifies to shippable.
    state.simulate()
    state.apply_autofix()
    assert all(r.passed for r in state.results)


def test_add_tools_empty_raises(tmp_path):
    state = StudioState(tmp_path)
    state.build(DEFAULT_SPEC)
    with pytest.raises(ValueError):
        state.add_tools([])
