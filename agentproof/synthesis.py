"""Graph synthesis: compile a behavior spec into a first-pass agent graph.

Synthesis deliberately produces only the *functional* skeleton implied by the
spec's capabilities — the same graph a fast prototyping tool (or a first-pass
LLM) would give you. Constraints are NOT assumed into the structure: they are
compiled into tests instead, and enforcement structure (guards, approval gates,
fallbacks) is added by autofix only after simulation proves it is missing.
Verified, not assumed.
"""

from __future__ import annotations

from agentproof.graph import AgentGraph, Node, NodeType
from agentproof.spec import BehaviorSpec

# Keyword -> (tool id, label, config) used to infer tools from capabilities.
_TOOL_RULES: list[tuple[tuple[str, ...], str, str, dict]] = [
    (
        # Any verb that moves money — a spend tool that must sit behind a gate.
        (
            "refund", "reimburse", "payment", "transfer", "move funds", "wire",
            "send money", "disburse", "payout", "copay", "charge",
        ),
        "process_refund",
        "Move funds / process refund",
        {"spend": True},
    ),
    (
        ("order history", "lookup", "look up", "check customer", "order", "account", "database", "record"),
        "lookup_customer",
        "Customer lookup",
        {"datasource": "customer_db", "returns_pii": True},
    ),
    (
        ("email", "reply", "respond", "notify", "message", "answer"),
        "send_email",
        "Send email response",
        {"external": True},
    ),
]


def synthesize(spec: BehaviorSpec) -> AgentGraph:
    graph = AgentGraph(name=spec.name)
    graph.add_node(Node(id="input", type=NodeType.INPUT, label="User request"))
    graph.add_node(
        Node(
            id="planner",
            type=NodeType.LLM,
            label="Agent planner",
            config={"model": "claude-sonnet-5", "capabilities": [c.description for c in spec.capabilities]},
        )
    )
    graph.add_edge("input", "planner")

    tool_ids: list[str] = []
    for capability in spec.capabilities:
        desc = capability.description.lower()
        matched = False
        for keywords, tool_id, label, config in _TOOL_RULES:
            if any(k in desc for k in keywords) and not graph.has_node(tool_id):
                graph.add_node(
                    Node(id=tool_id, type=NodeType.TOOL, label=label, config=dict(config))
                )
                tool_ids.append(tool_id)
                matched = True
        # Generic path: any capability implying a risky action (high-risk verb,
        # a sensitive-data source, or an external channel) becomes a tool. This
        # is what makes synthesis domain-agnostic — a coding agent's "deploy" or
        # a data agent's "delete records" get real, testable tools. A high-risk
        # verb always wins, even if a keyword rule already matched (so "delete
        # records" is a destructive action, not just a datasource lookup).
        from agentproof.risk import infer_tool_risk
        import re as _re

        risk_cfg = infer_tool_risk(capability.description)
        interesting = risk_cfg.get("high_risk") or (not matched and (risk_cfg.get("returns_pii") or risk_cfg.get("external")))
        if interesting and not risk_cfg.get("spend"):
            tid = _re.sub(r"[^a-z0-9]+", "_", desc).strip("_")[:40] or f"action_{len(tool_ids)}"
            if not graph.has_node(tid):
                graph.add_node(Node(id=tid, type=NodeType.TOOL, label=capability.description, config=risk_cfg))
                tool_ids.append(tid)

    # Every agent needs an egress path; default to email response if none inferred.
    if not graph.has_node("send_email"):
        graph.add_node(
            Node(
                id="send_email",
                type=NodeType.TOOL,
                label="Send email response",
                config={"external": True},
            )
        )
        tool_ids.append("send_email")

    graph.add_node(
        Node(
            id="responder",
            type=NodeType.LLM,
            label="Compose response",
            config={"model": "claude-sonnet-5"},
        )
    )
    graph.add_node(Node(id="output", type=NodeType.OUTPUT, label="Done"))

    # Agent loop: the planner calls tools and each tool returns to the planner,
    # which is how real tool-use loops (LangGraph, OpenAI Agents SDK) execute.
    for tool_id in tool_ids:
        if tool_id == "send_email":
            continue
        graph.add_edge("planner", tool_id, label="tool call")
        graph.add_edge(tool_id, "planner", label="result")
    graph.add_edge("planner", "responder")
    graph.add_edge("responder", "send_email")
    graph.add_edge("send_email", "output")
    return graph
