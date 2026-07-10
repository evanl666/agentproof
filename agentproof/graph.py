"""Agent graph model: nodes, edges, and the rewiring operations autofix needs."""

from __future__ import annotations

from collections import deque
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

    def path_avoiding(
        self,
        start_id: str,
        is_target: Callable[[Node], bool],
        is_barrier: Callable[[Node], bool],
    ) -> list[str] | None:
        """Shortest forward path from start to a target that never crosses a
        barrier node. Returns the node-id path, or None if the target is
        unreachable without crossing a barrier. This is the single source of
        truth for "is X gated by Y" — used by proofs, the simulator and autofix,
        so all three agree (a naive predecessor check is fooled by agent-loop
        back-edges and by chained guards)."""
        if not self.has_node(start_id):
            return None
        start = self.node(start_id)
        if is_barrier(start):
            return None
        parents: dict[str, str] = {}
        queue: deque[str] = deque([start_id])
        seen = {start_id}
        while queue:
            current = queue.popleft()
            node = self.node(current)
            if current != start_id and is_target(node):
                path = [current]
                while path[-1] != start_id:
                    path.append(parents[path[-1]])
                return list(reversed(path))
            for edge in self.edges:
                if edge.source != current or edge.target in seen:
                    continue
                target_node = self.node(edge.target)
                if is_barrier(target_node) and not is_target(target_node):
                    continue
                seen.add(edge.target)
                parents[edge.target] = current
                queue.append(edge.target)
        return None

    def is_gated(self, target_id: str, barrier: Callable[[Node], bool]) -> bool:
        """True if every path from an input node to target crosses a barrier."""
        entry = self.find(lambda n: n.type == NodeType.INPUT) or (self.nodes[0] if self.nodes else None)
        if entry is None:
            return True
        return self.path_avoiding(entry.id, lambda n: n.id == target_id, barrier) is None

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
        # A true deep copy — to_dict/from_dict alone would share the nested
        # `config` dicts by reference, so mutating a copied node's config would
        # corrupt the original (this bit autofix and mutation testing).
        import copy as _copy

        return AgentGraph(
            name=self.name,
            nodes=[Node(n.id, n.type, n.label, _copy.deepcopy(n.config)) for n in self.nodes],
            edges=[Edge(e.source, e.target, e.label) for e in self.edges],
        )
