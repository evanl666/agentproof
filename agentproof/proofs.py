"""Static reachability proofs — certainty to complement simulation.

Simulation says "I attacked it 50 times and nothing leaked." A proof says "no
path through this graph can leak, ever." AgentProof checks structural safety
properties directly on the graph: is there any path from the input to the money
tool that bypasses the approval gate? Any path from a PII source to an external
channel that skips redaction? If one exists, the proof fails and hands back the
exact bypassing path as a counterexample.

These are decidable graph-reachability questions, so the answer is definitive
where simulation is only empirical.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from agentproof.graph import AgentGraph, Node, NodeType
from agentproof.spec import BehaviorSpec, ConstraintKind


@dataclass
class Proof:
    kind: str
    property: str
    holds: bool
    detail: str
    counterexample: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "property": self.property,
            "holds": self.holds,
            "detail": self.detail,
            "counterexample": self.counterexample,
        }


def _path_avoiding(
    graph: AgentGraph,
    start_id: str,
    is_target: Callable[[Node], bool],
    is_barrier: Callable[[Node], bool],
) -> list[str] | None:
    """Shortest path from start to any target node that never passes through a
    barrier node. Returns the node-id path, or None if the target is
    unreachable without crossing a barrier."""
    if not graph.has_node(start_id):
        return None
    start = graph.node(start_id)
    # The start node itself being a target is only meaningful if it's reachable;
    # a barrier at the start still blocks (you can't "start inside" a guard).
    if is_barrier(start):
        return None
    parents: dict[str, str] = {}
    queue = deque([start_id])
    seen = {start_id}
    while queue:
        current = queue.popleft()
        node = graph.node(current)
        if current != start_id and is_target(node):
            path = [current]
            while path[-1] != start_id:
                path.append(parents[path[-1]])
            return list(reversed(path))
        for edge in graph.edges:
            if edge.source != current or edge.target in seen:
                continue
            target_node = graph.node(edge.target)
            # Can't traverse through a barrier — but the barrier can still be a
            # target endpoint (handled above before expanding).
            if is_barrier(target_node) and not is_target(target_node):
                continue
            seen.add(edge.target)
            parents[edge.target] = current
            queue.append(edge.target)
    return None


def _injection_guard(n: Node) -> bool:
    return n.type == NodeType.GUARD and n.config.get("kind") == "injection_guard"


def _pii_guard(n: Node) -> bool:
    return n.type == NodeType.GUARD and n.config.get("kind") == "pii_redaction"


def _memory_guard(n: Node) -> bool:
    return n.type == NodeType.GUARD and n.config.get("kind") == "memory_sanitizer"


def _condition(n: Node) -> bool:
    return n.type == NodeType.CONDITION and "threshold" in n.config


def prove(graph: AgentGraph, spec: BehaviorSpec) -> list[Proof]:
    """Check every structural safety property the spec implies."""
    proofs: list[Proof] = []
    entry = graph.find(lambda n: n.type == NodeType.INPUT)
    entry_id = entry.id if entry else (graph.nodes[0].id if graph.nodes else None)

    # 1. Money can never move without passing the approval gate.
    if spec.constraint(ConstraintKind.SPEND_LIMIT) and entry_id:
        for tool in graph.nodes:
            if not tool.config.get("spend"):
                continue
            bypass = _path_avoiding(
                graph, entry_id,
                is_target=lambda n, tid=tool.id: n.id == tid,
                is_barrier=_condition,
            )
            holds = bypass is None
            proofs.append(Proof(
                kind="spend_gated",
                property=f"every path to {tool.id} passes the approval gate",
                holds=holds,
                detail=("no ungated path to the money tool"
                        if holds else "an ungated path can move money"),
                counterexample=bypass or [],
            ))

    # 1b. Generic high-risk actions can never fire without an approval gate.
    if spec.constraint(ConstraintKind.HIGH_RISK_ACTION) and entry_id:
        for tool in graph.nodes:
            if not (tool.config.get("high_risk") and not tool.config.get("spend")):
                continue
            bypass = _path_avoiding(
                graph, entry_id,
                is_target=lambda n, tid=tool.id: n.id == tid,
                is_barrier=lambda n: n.type == NodeType.APPROVAL,
            )
            holds = bypass is None
            proofs.append(Proof(
                kind="high_risk_gated",
                property=f"every path to {tool.id} passes a human-approval gate",
                holds=holds,
                detail=("the high-risk action can't fire without approval"
                        if holds else "an unapproved path reaches this high-risk action"),
                counterexample=bypass or [],
            ))

    # 2. PII / sensitive data can never reach an external channel without redaction.
    if spec.constraint(ConstraintKind.PII_EGRESS) or spec.constraint(ConstraintKind.SENSITIVE_EGRESS):
        sources = [n for n in graph.nodes if n.config.get("returns_pii") or n.config.get("sensitive")]
        externals = [n for n in graph.nodes if n.config.get("external")]
        for src in sources:
            for ext in externals:
                bypass = _path_avoiding(
                    graph, src.id,
                    is_target=lambda n, eid=ext.id: n.id == eid,
                    is_barrier=_pii_guard,
                )
                holds = bypass is None
                proofs.append(Proof(
                    kind="pii_contained",
                    property=f"every path from {src.id} to {ext.id} is redacted",
                    holds=holds,
                    detail=("no unredacted PII path to an external channel"
                            if holds else "PII can reach an external channel unredacted"),
                    counterexample=bypass or [],
                ))

    # 3. Untrusted input can never reach the planner without a guard.
    if spec.constraint(ConstraintKind.PROMPT_INJECTION) and entry_id:
        planner = graph.find(lambda n: n.type == NodeType.LLM)
        if planner is not None:
            bypass = _path_avoiding(
                graph, entry_id,
                is_target=lambda n, pid=planner.id: n.id == pid,
                is_barrier=_injection_guard,
            )
            holds = bypass is None
            proofs.append(Proof(
                kind="injection_guarded",
                property=f"every path from input to {planner.id} is guarded",
                holds=holds,
                detail=("untrusted content is always quarantined before planning"
                        if holds else "injected instructions can reach the planner directly"),
                counterexample=bypass or [],
            ))

    # 4. Untrusted input can never reach the planner without memory sanitization.
    if spec.constraint(ConstraintKind.MEMORY_POISON) and entry_id:
        planner = graph.find(lambda n: n.type == NodeType.LLM)
        if planner is not None:
            bypass = _path_avoiding(
                graph, entry_id,
                is_target=lambda n, pid=planner.id: n.id == pid,
                is_barrier=_memory_guard,
            )
            holds = bypass is None
            proofs.append(Proof(
                kind="memory_guarded",
                property=f"every path from input to {planner.id} is memory-sanitized",
                holds=holds,
                detail=("untrusted content is sanitized before it can be persisted"
                        if holds else "untrusted content can be poisoned into long-term memory"),
                counterexample=bypass or [],
            ))

    return proofs


def all_hold(proofs: list[Proof]) -> bool:
    return all(p.holds for p in proofs)


def proof_summary(graph: AgentGraph, spec: BehaviorSpec) -> dict[str, Any]:
    proofs = prove(graph, spec)
    return {
        "total": len(proofs),
        "holding": sum(1 for p in proofs if p.holds),
        "failing": sum(1 for p in proofs if not p.holds),
        "all_hold": all_hold(proofs),
        "proofs": [p.to_dict() for p in proofs],
    }
