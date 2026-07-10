"""Policy visualizer: draw the red lines a spec implies onto the graph.

A behavior contract is easier to reason about as constraints drawn on the
canvas than as prose: "PII may never reach an external channel", "money moves
only through the approval gate". This module compiles the spec into policy
lines over the actual graph and reports, for each, whether the current
structure satisfies it — green when a guard/gate stands between the endpoints,
red when the path is open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentproof.graph import AgentGraph, NodeType
from agentproof.spec import BehaviorSpec, ConstraintKind


@dataclass
class PolicyLine:
    id: str
    kind: str
    source: str
    target: str
    label: str
    satisfied: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "target": self.target,
            "label": self.label,
            "satisfied": self.satisfied,
            "detail": self.detail,
        }


def _pii_guard(node) -> bool:
    return node.type == NodeType.GUARD and node.config.get("kind") == "pii_redaction"


def _injection_guard(node) -> bool:
    return node.type == NodeType.GUARD and node.config.get("kind") == "injection_guard"


def _condition(node) -> bool:
    return node.type == NodeType.CONDITION and "threshold" in node.config


def compute_policy_lines(graph: AgentGraph, spec: BehaviorSpec) -> list[PolicyLine]:
    lines: list[PolicyLine] = []

    # PII egress: every PII source must not reach an external node without a
    # redaction guard on the path between them.
    if spec.constraint(ConstraintKind.PII_EGRESS):
        sources = [n for n in graph.nodes if n.config.get("returns_pii")]
        externals = [n for n in graph.nodes if n.config.get("external")]
        for src in sources:
            for ext in externals:
                guarded = graph.upstream_has(ext.id, _pii_guard)
                lines.append(
                    PolicyLine(
                        id=f"pii-{src.id}-{ext.id}",
                        kind="pii_egress",
                        source=src.id,
                        target=ext.id,
                        label="PII must be redacted before this channel",
                        satisfied=guarded,
                        detail=(
                            "PII redaction guard present on the path"
                            if guarded
                            else "OPEN: customer data can reach this external channel unredacted"
                        ),
                    )
                )

    # Spend limit: every money-moving tool must sit behind a threshold gate.
    if spec.constraint(ConstraintKind.SPEND_LIMIT):
        spend_tools = [n for n in graph.nodes if n.config.get("spend")]
        entry = graph.find(lambda n: n.type == NodeType.INPUT)
        for tool in spend_tools:
            gated = graph.upstream_has(tool.id, _condition)
            lines.append(
                PolicyLine(
                    id=f"spend-{tool.id}",
                    kind="spend_limit",
                    source=entry.id if entry else tool.id,
                    target=tool.id,
                    label="Over-limit amounts must pass the approval gate",
                    satisfied=gated,
                    detail=(
                        "Threshold condition gate present upstream"
                        if gated
                        else "OPEN: this tool can move money with no limit check"
                    ),
                )
            )

    # Prompt injection: the planner must sit behind an injection guard.
    if spec.constraint(ConstraintKind.PROMPT_INJECTION):
        planner = graph.find(lambda n: n.type == NodeType.LLM)
        entry = graph.find(lambda n: n.type == NodeType.INPUT)
        if planner and entry:
            guarded = graph.upstream_has(planner.id, _injection_guard)
            lines.append(
                PolicyLine(
                    id=f"injection-{planner.id}",
                    kind="prompt_injection",
                    source=entry.id,
                    target=planner.id,
                    label="Untrusted content must be quarantined before planning",
                    satisfied=guarded,
                    detail=(
                        "Injection guard present between input and planner"
                        if guarded
                        else "OPEN: injected instructions reach the planner directly"
                    ),
                )
            )

    return lines


def policy_summary(graph: AgentGraph, spec: BehaviorSpec) -> dict[str, Any]:
    lines = compute_policy_lines(graph, spec)
    satisfied = sum(1 for line in lines if line.satisfied)
    return {
        "total": len(lines),
        "satisfied": satisfied,
        "open": len(lines) - satisfied,
        "lines": [line.to_dict() for line in lines],
    }
