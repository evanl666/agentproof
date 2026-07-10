"""Coverage 2.0 — production-grade coverage that speaks risk, not just nodes.

Node/edge coverage tells you what ran; it doesn't tell you whether the dangerous
parts were tested. Coverage 2.0 answers the questions an enterprise actually
asks: was every high-risk tool hit by an adversarial scenario? Is every
sensitive-data → external-channel path exercised? Was every approval and every
fallback path actually walked? Gaps here are the untested blast radius.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentproof.graph import AgentGraph, NodeType
from agentproof.scenarios import ScenarioCategory
from agentproof.simulator import SimulationResult

_ADVERSARIAL = {
    ScenarioCategory.ADVERSARIAL, ScenarioCategory.PROMPT_INJECTION,
    ScenarioCategory.PII_LEAK, ScenarioCategory.MEMORY_POISON,
    ScenarioCategory.CONTENT_POLICY, ScenarioCategory.BOUNDARY,
}


@dataclass
class RiskCoverageReport:
    high_risk_tool_coverage: float
    data_flow_coverage: float
    approval_path_coverage: float
    fallback_coverage: float
    uncovered_high_risk_tools: list[str] = field(default_factory=list)
    uncovered_data_flows: list[tuple[str, str]] = field(default_factory=list)
    uncovered_approvals: list[str] = field(default_factory=list)
    uncovered_fallbacks: list[str] = field(default_factory=list)

    @property
    def overall(self) -> float:
        return round(
            (self.high_risk_tool_coverage + self.data_flow_coverage
             + self.approval_path_coverage + self.fallback_coverage) / 4, 4
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "high_risk_tool_coverage": self.high_risk_tool_coverage,
            "data_flow_coverage": self.data_flow_coverage,
            "approval_path_coverage": self.approval_path_coverage,
            "fallback_coverage": self.fallback_coverage,
            "overall": self.overall,
            "uncovered_high_risk_tools": self.uncovered_high_risk_tools,
            "uncovered_data_flows": [list(x) for x in self.uncovered_data_flows],
            "uncovered_approvals": self.uncovered_approvals,
            "uncovered_fallbacks": self.uncovered_fallbacks,
        }


def _frac(covered: int, total: int) -> float:
    return round(covered / total, 4) if total else 1.0


def compute_risk_coverage(graph: AgentGraph, results: list[SimulationResult]) -> RiskCoverageReport:
    # Nodes visited by any *adversarial* scenario (a happy-path visit doesn't
    # count as testing a high-risk tool against attack).
    adversarial_visited: set[str] = set()
    all_visited: set[str] = set()
    for r in results:
        all_visited.update(r.visited_nodes)
        if r.scenario.category in _ADVERSARIAL or r.scenario.malicious or r.scenario.extra.get("high_risk_request"):
            adversarial_visited.update(r.visited_nodes)

    high_risk = [n for n in graph.nodes if n.type == NodeType.TOOL and (n.config.get("high_risk") or n.config.get("spend"))]
    uncovered_hr = [n.id for n in high_risk if n.id not in adversarial_visited]

    sources = [n for n in graph.nodes if n.config.get("returns_pii") or n.config.get("sensitive")]
    sinks = [n for n in graph.nodes if n.config.get("external")]
    flows = [(s.id, k.id) for s in sources for k in sinks]
    covered_flows = [(s, k) for (s, k) in flows if s in all_visited and k in all_visited]
    uncovered_flows = [f for f in flows if f not in covered_flows]

    approvals = graph.nodes_of_type(NodeType.APPROVAL)
    uncovered_appr = [n.id for n in approvals if n.id not in all_visited]

    fallbacks = graph.nodes_of_type(NodeType.FALLBACK)
    uncovered_fb = [n.id for n in fallbacks if n.id not in all_visited]

    return RiskCoverageReport(
        high_risk_tool_coverage=_frac(len(high_risk) - len(uncovered_hr), len(high_risk)),
        data_flow_coverage=_frac(len(covered_flows), len(flows)),
        approval_path_coverage=_frac(len(approvals) - len(uncovered_appr), len(approvals)),
        fallback_coverage=_frac(len(fallbacks) - len(uncovered_fb), len(fallbacks)),
        uncovered_high_risk_tools=uncovered_hr,
        uncovered_data_flows=uncovered_flows,
        uncovered_approvals=uncovered_appr,
        uncovered_fallbacks=uncovered_fb,
    )
