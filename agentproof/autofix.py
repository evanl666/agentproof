"""Graph auto-repair: turn failed simulations into structural fixes.

Each violation kind maps to a repair that adds enforcement *structure* to the
graph — condition gates, approval nodes, redaction guards, injection guards,
retries and fallbacks. The repaired graph is then re-simulated by the caller
to prove the fix actually closed the hole.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentproof.graph import AgentGraph, Node, NodeType
from agentproof.simulator import SimulationResult
from agentproof.spec import BehaviorSpec, ConstraintKind


@dataclass
class Fix:
    kind: str
    description: str
    nodes_added: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "nodes_added": self.nodes_added,
        }


@dataclass
class AutofixReport:
    graph: AgentGraph
    fixes: list[Fix] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"graph": self.graph.to_dict(), "fixes": [f.to_dict() for f in self.fixes]}


def _fix_spend_limit(graph: AgentGraph, spec: BehaviorSpec) -> Fix | None:
    refund_tool = graph.find(lambda n: n.type == NodeType.TOOL and n.config.get("spend"))
    limit = spec.constraint(ConstraintKind.SPEND_LIMIT)
    if refund_tool is None or limit is None:
        return None
    threshold = float(limit.params.get("threshold", 50.0))
    condition = Node(
        id="refund_policy_gate",
        type=NodeType.CONDITION,
        label=f"Amount ≤ ${threshold:.2f}?",
        config={"threshold": threshold, "field": "amount"},
    )
    graph.insert_before(refund_tool.id, condition)
    approval = Node(
        id="human_approval",
        type=NodeType.APPROVAL,
        label=f"Human approval (> ${threshold:.2f})",
        config={"escalates_to": "support_manager", "timeout_minutes": 60},
    )
    graph.add_node(approval)
    graph.add_edge(condition.id, approval.id, label=f"> ${threshold:.2f}")
    graph.add_edge(approval.id, refund_tool.id, label="approved")
    responder = graph.find(lambda n: n.type == NodeType.LLM and n.id != "planner")
    if responder is not None:
        graph.add_edge(approval.id, responder.id, label="rejected")
    return Fix(
        kind="policy_violation",
        description=(
            f"Added refund policy gate: amounts over ${threshold:.2f} now require "
            "human approval before the refund tool can execute"
        ),
        nodes_added=[condition.id, approval.id],
    )


def _fix_prompt_injection(graph: AgentGraph) -> Fix | None:
    entry = graph.find(lambda n: n.type == NodeType.INPUT)
    if entry is None:
        return None
    guard = Node(
        id="injection_guard",
        type=NodeType.GUARD,
        label="Prompt injection guard",
        config={"kind": "injection_guard", "action": "quarantine_untrusted_content"},
    )
    graph.insert_after(entry.id, guard)
    return Fix(
        kind="prompt_injection",
        description=(
            "Added prompt injection guard after input: instructions embedded in "
            "user-provided content are quarantined, never executed"
        ),
        nodes_added=[guard.id],
    )


def _fix_pii_egress(graph: AgentGraph) -> Fix | None:
    external_tools = [
        n for n in graph.nodes if n.type == NodeType.TOOL and n.config.get("external")
    ]
    if not external_tools:
        return None
    added: list[str] = []
    for tool in external_tools:
        guard_id = f"pii_redaction_{tool.id}"
        if graph.has_node(guard_id):
            continue
        guard = Node(
            id=guard_id,
            type=NodeType.GUARD,
            label="PII redaction",
            config={"kind": "pii_redaction", "fields": ["email", "address", "phone", "card"]},
        )
        graph.insert_before(tool.id, guard)
        added.append(guard_id)
    if not added:
        return None
    return Fix(
        kind="pii_egress",
        description=(
            "Added PII redaction guard before every external channel: customer "
            "data is scrubbed before anything leaves the system"
        ),
        nodes_added=added,
    )


def _fix_tool_failures(graph: AgentGraph, failing_tools: set[str]) -> Fix | None:
    added: list[str] = []
    for tool_id in sorted(failing_tools):
        if not graph.has_node(tool_id):
            continue
        tool = graph.node(tool_id)
        tool.config["retry"] = {"max_attempts": 3, "backoff_seconds": 2}
        fallback_id = f"fallback_{tool_id}"
        if graph.has_node(fallback_id):
            continue
        fallback = Node(
            id=fallback_id,
            type=NodeType.FALLBACK,
            label=f"Fallback for {tool.label}",
            config={"strategy": "graceful_degradation"},
        )
        graph.add_node(fallback)
        graph.add_edge(tool_id, fallback_id, label="on error")
        if tool.config.get("external"):
            target = graph.find(lambda n: n.type == NodeType.OUTPUT)
        else:
            target = graph.find(lambda n: n.type == NodeType.LLM and n.id != "planner")
        if target is not None:
            graph.add_edge(fallback_id, target.id)
        added.append(fallback_id)
    if not added:
        return None
    return Fix(
        kind="unhandled_tool_error",
        description=(
            "Added retry (3 attempts, backoff) and a fallback path for every tool "
            "that failed in simulation; the agent now degrades gracefully"
        ),
        nodes_added=added,
    )


def autofix(
    graph: AgentGraph, spec: BehaviorSpec, results: list[SimulationResult]
) -> AutofixReport:
    """Repair the graph based on observed violations. Returns a new graph."""
    repaired = graph.copy()
    kinds = {v.kind for r in results for v in r.violations}
    failing_tools = {
        v.node_id
        for r in results
        for v in r.violations
        if v.kind == "unhandled_tool_error" and v.node_id
    }
    fixes: list[Fix] = []

    if "prompt_injection" in kinds:
        fix = _fix_prompt_injection(repaired)
        if fix:
            fixes.append(fix)
    if "policy_violation" in kinds:
        fix = _fix_spend_limit(repaired, spec)
        if fix:
            fixes.append(fix)
    if "pii_egress" in kinds:
        fix = _fix_pii_egress(repaired)
        if fix:
            fixes.append(fix)
    if "unhandled_tool_error" in kinds:
        fix = _fix_tool_failures(repaired, failing_tools)
        if fix:
            fixes.append(fix)

    return AutofixReport(graph=repaired, fixes=fixes)
