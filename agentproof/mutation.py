"""Mutation testing for agents — does your test suite actually kill bad behavior?

A passing suite tells you the agent behaves *today*. Mutation testing asks the
harder question: if the agent silently regressed, would your scenarios catch it?
We inject faults into a *verified* graph — remove a guard, drop an approval gate,
delete a fallback, weaken a spend limit — and re-run the suite. A good suite
"kills" each mutant (some scenario now fails). Surviving mutants are blind spots:
regressions your tests would wave through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentproof.graph import AgentGraph, NodeType
from agentproof.scenarios import Scenario
from agentproof.simulator import run_suite
from agentproof.spec import BehaviorSpec


@dataclass
class Mutant:
    id: str
    description: str
    killed: bool = False
    killed_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "description": self.description,
                "killed": self.killed, "killed_by": self.killed_by[:5]}


@dataclass
class MutationReport:
    mutants: list[Mutant]

    @property
    def score(self) -> float:
        if not self.mutants:
            return 1.0
        return round(sum(1 for m in self.mutants if m.killed) / len(self.mutants), 4)

    @property
    def survivors(self) -> list[Mutant]:
        return [m for m in self.mutants if not m.killed]

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "total": len(self.mutants),
                "killed": sum(1 for m in self.mutants if m.killed),
                "mutants": [m.to_dict() for m in self.mutants]}


def _mutations(graph: AgentGraph):
    """Yield (mutant_id, description, mutated_graph) — each a single fault."""
    # Remove each guard.
    for guard in graph.nodes_of_type(NodeType.GUARD):
        g = graph.copy()
        _remove_node(g, guard.id)
        yield f"remove_guard:{guard.id}", f"removed guard {guard.id}", g
    # Remove each approval gate.
    for appr in graph.nodes_of_type(NodeType.APPROVAL):
        g = graph.copy()
        _remove_node(g, appr.id)
        yield f"remove_approval:{appr.id}", f"removed approval gate {appr.id}", g
    # Remove each condition (spend gate).
    for cond in graph.nodes_of_type(NodeType.CONDITION):
        g = graph.copy()
        _remove_node(g, cond.id)
        yield f"remove_condition:{cond.id}", f"removed spend gate {cond.id}", g
    # Drop each fallback.
    for fb in graph.nodes_of_type(NodeType.FALLBACK):
        g = graph.copy()
        _remove_node(g, fb.id)
        yield f"remove_fallback:{fb.id}", f"removed fallback {fb.id}", g
    # Weaken each spend threshold (10x).
    for cond in graph.nodes_of_type(NodeType.CONDITION):
        if "threshold" in cond.config:
            g = graph.copy()
            g.node(cond.id).config["threshold"] = float(cond.config["threshold"]) * 100
            yield f"weaken_limit:{cond.id}", f"raised {cond.id} limit 100x", g


def _remove_node(graph: AgentGraph, node_id: str) -> None:
    """Splice a node out, reconnecting its predecessors to its successors."""
    preds = [e.source for e in graph.edges if e.target == node_id]
    succs = [e.target for e in graph.edges if e.source == node_id]
    graph.edges = [e for e in graph.edges if e.source != node_id and e.target != node_id]
    graph.nodes = [n for n in graph.nodes if n.id != node_id]
    for p in preds:
        for s in succs:
            if not any(e.source == p and e.target == s for e in graph.edges):
                graph.add_edge(p, s)


def mutation_test(graph: AgentGraph, spec: BehaviorSpec, scenarios: list[Scenario]) -> MutationReport:
    """Inject faults into a verified graph; a mutant is killed if the suite,
    which passes on the original, now has a failing scenario."""
    baseline = run_suite(graph, spec, scenarios)
    baseline_fail = {r.scenario.id for r in baseline if not r.passed}
    mutants: list[Mutant] = []
    for mid, desc, mutated in _mutations(graph):
        results = run_suite(mutated, spec, scenarios)
        newly_failing = [r.scenario.id for r in results if not r.passed and r.scenario.id not in baseline_fail]
        mutants.append(Mutant(id=mid, description=desc, killed=bool(newly_failing), killed_by=newly_failing))
    return MutationReport(mutants=mutants)
