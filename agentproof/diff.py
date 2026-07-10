"""Behavior diff: what actually changed between two versions of an agent.

Not a graph diff — a *behavior* diff. Both versions replay the exact same
scenario suite, and the diff reports newly passing/failing scenarios, risk
movement, new tools and cost drift. This is what belongs in a PR description
when someone edits a prompt or swaps a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentproof.coverage import compute_coverage
from agentproof.graph import AgentGraph, NodeType
from agentproof.scenarios import Scenario
from agentproof.score import compute_score
from agentproof.simulator import run_suite
from agentproof.spec import BehaviorSpec


@dataclass
class BehaviorDiff:
    newly_passing: list[str] = field(default_factory=list)
    newly_failing: list[str] = field(default_factory=list)
    tools_added: list[str] = field(default_factory=list)
    tools_removed: list[str] = field(default_factory=list)
    guards_added: list[str] = field(default_factory=list)
    risk_before: int = 0
    risk_after: int = 0
    cost_before_tokens: int = 0
    cost_after_tokens: int = 0
    score_before: int = 0
    score_after: int = 0

    @property
    def cost_delta_pct(self) -> float:
        if self.cost_before_tokens == 0:
            return 0.0
        return round(
            (self.cost_after_tokens - self.cost_before_tokens)
            / self.cost_before_tokens
            * 100,
            1,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "newly_passing": self.newly_passing,
            "newly_failing": self.newly_failing,
            "tools_added": self.tools_added,
            "tools_removed": self.tools_removed,
            "guards_added": self.guards_added,
            "risk_before": self.risk_before,
            "risk_after": self.risk_after,
            "cost_before_tokens": self.cost_before_tokens,
            "cost_after_tokens": self.cost_after_tokens,
            "cost_delta_pct": self.cost_delta_pct,
            "score_before": self.score_before,
            "score_after": self.score_after,
        }


def _risk(results: list) -> int:
    """Share of scenarios ending in at least one violation, 0-100."""
    if not results:
        return 0
    return round(sum(1 for r in results if not r.passed) / len(results) * 100)


def behavior_diff(
    spec: BehaviorSpec,
    before: AgentGraph,
    after: AgentGraph,
    scenarios: list[Scenario],
) -> BehaviorDiff:
    results_before = run_suite(before, spec, scenarios)
    results_after = run_suite(after, spec, scenarios)

    before_by_id = {r.scenario.id: r for r in results_before}
    after_by_id = {r.scenario.id: r for r in results_after}

    newly_passing = [
        sid
        for sid, r in after_by_id.items()
        if r.passed and not before_by_id[sid].passed
    ]
    newly_failing = [
        sid
        for sid, r in after_by_id.items()
        if not r.passed and before_by_id[sid].passed
    ]

    tools_before = {n.id for n in before.nodes_of_type(NodeType.TOOL)}
    tools_after = {n.id for n in after.nodes_of_type(NodeType.TOOL)}
    guards_before = {n.id for n in before.nodes_of_type(NodeType.GUARD)}
    guards_before |= {n.id for n in before.nodes_of_type(NodeType.APPROVAL)}
    guards_after = {n.id for n in after.nodes_of_type(NodeType.GUARD)}
    guards_after |= {n.id for n in after.nodes_of_type(NodeType.APPROVAL)}

    score_before = compute_score(results_before, compute_coverage(before, results_before))
    score_after = compute_score(results_after, compute_coverage(after, results_after))

    return BehaviorDiff(
        newly_passing=sorted(newly_passing),
        newly_failing=sorted(newly_failing),
        tools_added=sorted(tools_after - tools_before),
        tools_removed=sorted(tools_before - tools_after),
        guards_added=sorted(guards_after - guards_before),
        risk_before=_risk(results_before),
        risk_after=_risk(results_after),
        cost_before_tokens=sum(r.cost_tokens for r in results_before),
        cost_after_tokens=sum(r.cost_tokens for r in results_after),
        score_before=score_before.overall,
        score_after=score_after.overall,
    )
