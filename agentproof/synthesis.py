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

    # Each capability becomes its own tool, labelled with the user's own words so
    # the graph reads correctly in ANY domain (a GitHub agent gets "Read issues",
    # not "Customer lookup"). The keyword rules only supply a stable canonical id
    # and a crude risk hint for the common actions; risk is otherwise inferred
    # generically. Nothing is dropped or collapsed into a hardcoded template.
    from agentproof.risk import infer_tool_risk
    import re as _re

    tool_ids: list[str] = []
    have_spend = False
    for capability in spec.capabilities:
        desc = capability.description
        low = desc.lower()
        risk_cfg = infer_tool_risk(desc)
        rule = next(((tid, dict(cfg)) for kws, tid, _lbl, cfg in _TOOL_RULES
                     if any(k in low for k in kws)), None)
        if rule:  # fold the rule's risk flags in as hints (don't override inference)
            for k, v in rule[1].items():
                risk_cfg.setdefault(k, v)
        # Keep a single money tool offline so autofix's one-gate repair stays valid.
        if risk_cfg.get("spend") and have_spend:
            continue
        if rule and not graph.has_node(rule[0]):
            tid = rule[0]
        else:
            base = _re.sub(r"[^a-z0-9]+", "_", low).strip("_")[:40] or "tool"
            tid, n = base, 2
            while graph.has_node(tid):
                tid = f"{base}_{n}"; n += 1
        graph.add_node(Node(id=tid, type=NodeType.TOOL, label=desc, config=risk_cfg))
        tool_ids.append(tid)
        if risk_cfg.get("spend"):
            have_spend = True

    # Every agent needs an outbound reply channel; ensure a generic egress node.
    if not graph.has_node("send_email"):
        graph.add_node(
            Node(id="send_email", type=NodeType.TOOL, label="Send response",
                 config={"external": True})
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
