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


def _fix_memory_poison(graph: AgentGraph) -> Fix | None:
    entry = graph.find(lambda n: n.type == NodeType.INPUT)
    if entry is None or graph.has_node("memory_sanitizer"):
        return None
    guard = Node(
        id="memory_sanitizer",
        type=NodeType.GUARD,
        label="Memory sanitizer",
        config={"kind": "memory_sanitizer", "action": "sanitize_before_persist"},
    )
    graph.insert_after(entry.id, guard)
    return Fix(
        kind="memory_poison",
        description=(
            "Added memory sanitizer after input: untrusted content is cleaned "
            "before it can be written to long-term memory and weaponized later"
        ),
        nodes_added=[guard.id],
    )


def _fix_pii_egress(graph: AgentGraph, kind: str = "pii_egress") -> Fix | None:
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
            label="Sensitive-data redaction",
            config={"kind": "pii_redaction", "fields": ["email", "address", "phone", "card", "secret", "token"]},
        )
        graph.insert_before(tool.id, guard)
        added.append(guard_id)
    if not added:
        return None
    label = "PII" if kind == "pii_egress" else "sensitive data"
    return Fix(
        kind=kind,
        description=(
            f"Added redaction guard before every external channel: {label} "
            "is scrubbed before anything leaves the system"
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
    if "memory_poison" in kinds:
        fix = _fix_memory_poison(repaired)
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

    if "unauthorized_action" in kinds:
        fix = _fix_high_risk_action(repaired)
        if fix:
            fixes.append(fix)
    if "sensitive_egress" in kinds:
        fix = _fix_pii_egress(repaired, kind="sensitive_egress")
        if fix:
            fixes.append(fix)

    for kind in sorted(k for k in kinds if k.startswith("content_policy_")):
        fix = _fix_content_policy(repaired, kind)
        if fix:
            fixes.append(fix)

    return AutofixReport(graph=repaired, fixes=fixes)


def _fix_high_risk_action(graph: AgentGraph) -> Fix | None:
    """Insert a human-approval node before every non-money high-risk tool."""
    tools = [
        n for n in graph.nodes
        if n.type == NodeType.TOOL and n.config.get("high_risk") and not n.config.get("spend")
    ]
    added: list[str] = []
    for tool in tools:
        # Path-based gating check — consistent with the simulator and proofs.
        # (A direct-predecessor check is fooled by chained guards; upstream_has
        # is fooled by agent-loop back-edges through other tools' gates.)
        if graph.is_gated(tool.id, lambda n: n.type == NodeType.APPROVAL):
            continue
        approval = Node(
            id=f"approval_{tool.id}",
            type=NodeType.APPROVAL,
            label=f"Human approval for {tool.label}",
            config={"escalates_to": "reviewer", "reason": tool.config.get("risk_category", "high_risk")},
        )
        graph.insert_before(tool.id, approval)
        added.append(approval.id)
    if not added:
        return None
    return Fix(
        kind="unauthorized_action",
        description=(
            "Added a human-approval gate before every high-risk action "
            "(delete/deploy/grant/...): irreversible actions can't fire unapproved"
        ),
        nodes_added=added,
    )


def _fix_content_policy(graph: AgentGraph, violation_kind: str) -> Fix | None:
    from agentproof.plugins import plugin_for_kind

    plugin = plugin_for_kind(violation_kind[len("content_policy_"):])
    if plugin is None:
        return None
    external_tools = [
        n for n in graph.nodes if n.type == NodeType.TOOL and n.config.get("external")
    ]
    added: list[str] = []
    for tool in external_tools:
        guard_id = f"{plugin.guard_id}_{tool.id}"
        if graph.has_node(guard_id):
            continue
        guard = Node(
            id=guard_id,
            type=NodeType.GUARD,
            label=plugin.guard_label,
            config={"kind": plugin.guard_kind, "plugin": plugin.kind},
        )
        graph.insert_before(tool.id, guard)
        added.append(guard_id)
    if not added:
        return None
    return Fix(
        kind=violation_kind,
        description=(
            f"Added {plugin.guard_label} before every external channel: "
            f"'{plugin.kind}' is now enforced before anything leaves the system"
        ),
        nodes_added=added,
    )
