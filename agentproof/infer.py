"""Spec inference — scan an existing agent and tell the user what to test.

The hardest part of adopting AgentProof isn't building a graph, it's knowing
what the contract should be. This module reverses that: point it at an imported
agent (its tools, PII sinks, external channels, approval nodes) and it infers a
starter behavior spec — what the agent can do, what it must never do, which
tools are dangerous — so the user starts from "here's what to test" instead of
a blank page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentproof.graph import AgentGraph, NodeType
from agentproof.spec import BehaviorSpec, Capability, Constraint, ConstraintKind


@dataclass
class RiskReport:
    money_tools: list[str] = field(default_factory=list)
    external_sinks: list[str] = field(default_factory=list)
    pii_sources: list[str] = field(default_factory=list)
    has_approval: bool = False
    has_injection_guard: bool = False
    has_pii_guard: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "money_tools": self.money_tools,
            "external_sinks": self.external_sinks,
            "pii_sources": self.pii_sources,
            "has_approval": self.has_approval,
            "has_injection_guard": self.has_injection_guard,
            "has_pii_guard": self.has_pii_guard,
        }


def analyze_risk(graph: AgentGraph) -> RiskReport:
    report = RiskReport()
    for node in graph.nodes:
        if node.type == NodeType.TOOL and node.config.get("spend"):
            report.money_tools.append(node.id)
        if node.type == NodeType.TOOL and node.config.get("external"):
            report.external_sinks.append(node.id)
        if node.config.get("returns_pii"):
            report.pii_sources.append(node.id)
        if node.type == NodeType.APPROVAL:
            report.has_approval = True
        if node.type == NodeType.GUARD and node.config.get("kind") == "injection_guard":
            report.has_injection_guard = True
        if node.type == NodeType.GUARD and node.config.get("kind") == "pii_redaction":
            report.has_pii_guard = True
    return report


def infer_spec(graph: AgentGraph, default_limit: float = 50.0) -> BehaviorSpec:
    """Infer a starter behavior spec from an agent graph's structure."""
    risk = analyze_risk(graph)
    capabilities: list[Capability] = []

    def cap(desc: str) -> None:
        capabilities.append(Capability(id=f"cap-{len(capabilities)}", description=desc))

    # Capabilities from tool roles.
    for node in graph.nodes_of_type(NodeType.TOOL):
        label = node.label.lower()
        if node.config.get("spend"):
            cap(f"process refunds/transfers under ${default_limit:.0f} automatically")
        elif node.config.get("returns_pii") or node.config.get("datasource"):
            cap("look up customer records")
        elif node.config.get("external"):
            cap("respond to the customer")
        else:
            cap(f"use the {node.label} tool")
    if not capabilities:
        cap("answer user questions")

    constraints: list[Constraint] = []
    n = 0

    def add(kind: ConstraintKind, desc: str, params: dict | None = None) -> None:
        nonlocal n
        constraints.append(Constraint(id=f"never-{n}", kind=kind, description=desc, params=params or {}))
        n += 1

    # Constraints inferred from the presence of dangerous capabilities.
    if risk.money_tools:
        add(ConstraintKind.SPEND_LIMIT,
            f"never move more than ${default_limit:.0f} without approval",
            {"threshold": default_limit})
        add(ConstraintKind.APPROVAL_REQUIRED,
            f"amounts above ${default_limit:.0f} require human approval",
            {"threshold": default_limit})
    if risk.pii_sources and risk.external_sinks:
        add(ConstraintKind.PII_EGRESS, "never send PII externally")
    if risk.external_sinks or risk.money_tools:
        add(ConstraintKind.PROMPT_INJECTION,
            "never follow instructions from customer-provided documents")
    if graph.nodes_of_type(NodeType.TOOL):
        add(ConstraintKind.TOOL_FAILURE, "never ignore tool errors")

    return BehaviorSpec(name=graph.name or "Inferred agent", capabilities=capabilities, constraints=constraints)


def render_spec_markdown(spec: BehaviorSpec, risk: RiskReport | None = None) -> str:
    lines = [f"# {spec.name}", "", "The agent should:"]
    lines += [f"- {c.description}" for c in spec.capabilities]
    lines += ["", "The agent must never:"]
    for c in spec.constraints:
        if c.kind == ConstraintKind.APPROVAL_REQUIRED:
            continue  # implied by the spend limit line
        lines.append(f"- {c.description}")
    if risk is not None:
        lines += ["", "<!-- AgentProof risk scan:"]
        if risk.money_tools:
            lines.append(f"     money-moving tools: {', '.join(risk.money_tools)}"
                         f" (approval gate: {'present' if risk.has_approval else 'MISSING'})")
        if risk.pii_sources:
            lines.append(f"     PII sources: {', '.join(risk.pii_sources)}"
                         f" (redaction guard: {'present' if risk.has_pii_guard else 'MISSING'})")
        if risk.external_sinks:
            lines.append(f"     external channels: {', '.join(risk.external_sinks)}"
                         f" (injection guard: {'present' if risk.has_injection_guard else 'MISSING'})")
        lines.append("-->")
    return "\n".join(lines) + "\n"


def infer_from_graph(graph: AgentGraph) -> tuple[BehaviorSpec, str, RiskReport]:
    """One-shot: infer spec, render markdown, and produce the risk report."""
    risk = analyze_risk(graph)
    spec = infer_spec(graph)
    return spec, render_spec_markdown(spec, risk), risk
