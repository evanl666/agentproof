"""Agent graph model: nodes, edges, and the rewiring operations autofix needs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class NodeType(str, Enum):
    INPUT = "input"
    LLM = "llm"
    TOOL = "tool"
    CONDITION = "condition"
    APPROVAL = "approval"
    GUARD = "guard"
    FALLBACK = "fallback"
    OUTPUT = "output"


@dataclass
class Node:
    id: str
    type: NodeType
    label: str
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "label": self.label,
            "config": self.config,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Node":
        return Node(
            id=data["id"],
            type=NodeType(data["type"]),
            label=data["label"],
            config=data.get("config", {}),
        )


@dataclass
class Edge:
    source: str
    target: str
    label: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.source, self.target)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "target": self.target, "label": self.label}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Edge":
        return Edge(source=data["source"], target=data["target"], label=data.get("label", ""))


@dataclass
class AgentGraph:
    name: str
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(f"No node with id {node_id!r}")

    def has_node(self, node_id: str) -> bool:
        return any(n.id == node_id for n in self.nodes)

    def add_node(self, node: Node) -> Node:
        if self.has_node(node.id):
            raise ValueError(f"Duplicate node id {node.id!r}")
        self.nodes.append(node)
        return node

    def add_edge(self, source: str, target: str, label: str = "") -> Edge:
        edge = Edge(source=source, target=target, label=label)
        self.edges.append(edge)
        return edge

    def nodes_of_type(self, node_type: NodeType) -> list[Node]:
        return [n for n in self.nodes if n.type == node_type]

    def successors(self, node_id: str) -> list[Node]:
        return [self.node(e.target) for e in self.edges if e.source == node_id]

    def predecessors(self, node_id: str) -> list[Node]:
        return [self.node(e.source) for e in self.edges if e.target == node_id]

    def find(self, predicate: Callable[[Node], bool]) -> Node | None:
        for n in self.nodes:
            if predicate(n):
                return n
        return None

    def insert_before(self, target_id: str, new_node: Node) -> Node:
        """Insert new_node on every incoming edge of target_id."""
        self.add_node(new_node)
        for edge in self.edges:
            if edge.target == target_id:
                edge.target = new_node.id
        self.add_edge(new_node.id, target_id)
        return new_node

    def insert_after(self, source_id: str, new_node: Node) -> Node:
        """Insert new_node on every outgoing edge of source_id."""
        self.add_node(new_node)
        for edge in self.edges:
            if edge.source == source_id:
                edge.source = new_node.id
        self.add_edge(source_id, new_node.id)
        return new_node

    def upstream_has(self, node_id: str, predicate: Callable[[Node], bool]) -> bool:
        """True if any ancestor of node_id satisfies predicate."""
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            for pred in self.predecessors(current):
                if pred.id in seen:
                    continue
                seen.add(pred.id)
                if predicate(pred):
                    return True
                stack.append(pred.id)
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "AgentGraph":
        return AgentGraph(
            name=data["name"],
            nodes=[Node.from_dict(n) for n in data["nodes"]],
            edges=[Edge.from_dict(e) for e in data["edges"]],
        )

    def copy(self) -> "AgentGraph":
        return AgentGraph.from_dict(self.to_dict())
