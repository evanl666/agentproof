"""Agent path coverage: which parts of the graph have never been exercised.

Like code coverage, but for agent structure: nodes, edges, tool paths and
approval paths. "It ran once in the happy path" is not "it was tested."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentproof.graph import AgentGraph, NodeType
from agentproof.simulator import SimulationResult


@dataclass
class CoverageReport:
    node_coverage: float
    edge_coverage: float
    visited_nodes: list[str] = field(default_factory=list)
    unvisited_nodes: list[str] = field(default_factory=list)
    visited_edges: list[tuple[str, str]] = field(default_factory=list)
    unvisited_edges: list[tuple[str, str]] = field(default_factory=list)
    untested_tools: list[str] = field(default_factory=list)
    approval_paths_tested: bool = False

    @property
    def overall(self) -> float:
        return round((self.node_coverage + self.edge_coverage) / 2, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_coverage": self.node_coverage,
            "edge_coverage": self.edge_coverage,
            "overall": self.overall,
            "visited_nodes": self.visited_nodes,
            "unvisited_nodes": self.unvisited_nodes,
            "visited_edges": [list(e) for e in self.visited_edges],
            "unvisited_edges": [list(e) for e in self.unvisited_edges],
            "untested_tools": self.untested_tools,
            "approval_paths_tested": self.approval_paths_tested,
        }


def compute_coverage(graph: AgentGraph, results: list[SimulationResult]) -> CoverageReport:
    visited_nodes: set[str] = set()
    visited_edges: set[tuple[str, str]] = set()
    approvals = False
    for result in results:
        visited_nodes.update(result.visited_nodes)
        visited_edges.update(result.visited_edges)
        approvals = approvals or result.approval_requested

    all_nodes = {n.id for n in graph.nodes}
    all_edges = {e.key for e in graph.edges}
    unvisited_nodes = sorted(all_nodes - visited_nodes)
    unvisited_edges = sorted(all_edges - visited_edges)
    untested_tools = [
        n.id for n in graph.nodes_of_type(NodeType.TOOL) if n.id in unvisited_nodes
    ]

    return CoverageReport(
        node_coverage=round(len(visited_nodes & all_nodes) / max(len(all_nodes), 1), 4),
        edge_coverage=round(len(visited_edges & all_edges) / max(len(all_edges), 1), 4),
        visited_nodes=sorted(visited_nodes & all_nodes),
        unvisited_nodes=unvisited_nodes,
        visited_edges=sorted(visited_edges & all_edges),
        unvisited_edges=unvisited_edges,
        untested_tools=untested_tools,
        approval_paths_tested=approvals,
    )
