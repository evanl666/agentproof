"""Import existing agents into AgentProof.

You don't have to build your agent here to prove it here. Importers lift
agents written elsewhere into the AgentProof graph model so they can be
simulated, scored, auto-fixed and re-exported:

- LangGraph Python source (static AST analysis of add_node/add_edge calls)
- Flowise chatflow JSON exports
- Generic node/edge JSON (n8n-style, or AgentProof's own format)
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from agentproof.graph import AgentGraph, Edge, Node, NodeType

_TOOL_HINTS = {
    "refund": {"spend": True},
    "payment": {"spend": True},
    "charge": {"spend": True},
    "lookup": {"datasource": "db", "returns_pii": True},
    "customer": {"datasource": "db", "returns_pii": True},
    "search": {"datasource": "db"},
    "email": {"external": True},
    "mail": {"external": True},
    "send": {"external": True},
    "slack": {"external": True},
    "webhook": {"external": True},
    "http": {"external": True},
}


def _has_word(text: str, *keywords: str) -> bool:
    return any(re.search(rf"\b{re.escape(k)}\b", text) for k in keywords)


def _infer_node_type(name: str, hint: str = "") -> tuple[NodeType, dict[str, Any]]:
    raw = f"{name} {hint}"
    # Split camelCase and snake-case so "sendEmail"/"send_email" become words.
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw)
    text = re.sub(r"[_\-/]", " ", text).lower()
    if _has_word(text, "input", "start", "trigger", "entry"):
        return NodeType.INPUT, {}
    if _has_word(text, "output", "end", "finish", "done"):
        return NodeType.OUTPUT, {}
    if _has_word(text, "approval", "review", "human"):
        return NodeType.APPROVAL, {}
    if _has_word(text, "guard", "redact", "sanitize", "moderation"):
        kind = "pii_redaction" if _has_word(text, "redact", "pii") else "injection_guard"
        return NodeType.GUARD, {"kind": kind}
    if _has_word(text, "condition", "branch", "router", "if", "switch", "gate"):
        return NodeType.CONDITION, {}
    if _has_word(text, "llm", "chat", "agent", "model", "planner", "prompt", "compose", "respond"):
        return NodeType.LLM, {"model": "imported"}
    config: dict[str, Any] = {}
    for keyword, extra in _TOOL_HINTS.items():
        if keyword in text:
            config.update(extra)
    return NodeType.TOOL, config


def import_langgraph(source: str, name: str = "Imported LangGraph agent") -> AgentGraph:
    """Statically analyze LangGraph Python source and lift its StateGraph.

    Recognizes graph.add_node("id", ...), graph.add_edge("a", "b"),
    graph.add_conditional_edges("a", fn, {...}), graph.set_entry_point("x"),
    and the START/END sentinels.
    """
    tree = ast.parse(source)
    graph = AgentGraph(name=name)
    edges: list[tuple[str, str, str]] = []
    entry: str | None = None

    def const(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in ("START", "END"):
            return node.id
        if isinstance(node, ast.Attribute) and node.attr in ("START", "END"):
            return node.attr
        return None

    for stmt in ast.walk(tree):
        if not isinstance(stmt, ast.Call) or not isinstance(stmt.func, ast.Attribute):
            continue
        method = stmt.func.attr
        args = stmt.args
        if method == "add_node" and args:
            node_id = const(args[0])
            if node_id and not graph.has_node(node_id):
                node_type, config = _infer_node_type(node_id)
                graph.add_node(Node(id=node_id, type=node_type, label=node_id, config=config))
        elif method == "add_edge" and len(args) >= 2:
            a, b = const(args[0]), const(args[1])
            if a and b:
                edges.append((a, b, ""))
        elif method == "add_conditional_edges" and args:
            a = const(args[0])
            mapping = next((x for x in args if isinstance(x, ast.Dict)), None)
            if a and mapping is not None:
                for key, value in zip(mapping.keys, mapping.values):
                    label = const(key) or ""
                    b = const(value)
                    if b:
                        edges.append((a, b, label))
        elif method in ("set_entry_point", "set_finish_point") and args:
            point = const(args[0])
            if point and method == "set_entry_point":
                entry = point

    # Materialize START/END sentinels and the entry point as input/output nodes.
    def ensure(node_id: str, node_type: NodeType, label: str) -> str:
        if not graph.has_node(node_id):
            graph.add_node(Node(id=node_id, type=node_type, label=label))
        return node_id

    for a, b, label in edges:
        if a == "START":
            a = ensure("input", NodeType.INPUT, "User request")
        if b == "END":
            b = ensure("output", NodeType.OUTPUT, "Done")
        for node_id in (a, b):
            if not graph.has_node(node_id):
                node_type, config = _infer_node_type(node_id)
                graph.add_node(Node(id=node_id, type=node_type, label=node_id, config=config))
        graph.edges.append(Edge(source=a, target=b, label=label))
    if entry:
        start = ensure("input", NodeType.INPUT, "User request")
        if not any(e.source == start and e.target == entry for e in graph.edges):
            graph.add_edge(start, entry)
    if not graph.nodes_of_type(NodeType.INPUT):
        # Fall back: mark sources with no incoming edges as entry.
        targets = {e.target for e in graph.edges}
        for node in graph.nodes:
            if node.id not in targets and node.type != NodeType.OUTPUT:
                node.type = NodeType.INPUT
                break
    return graph


def import_flowise(data: dict[str, Any], name: str | None = None) -> AgentGraph:
    """Import a Flowise chatflow JSON export ({"nodes": [...], "edges": [...]})."""
    graph = AgentGraph(name=name or data.get("name", "Imported Flowise agent"))
    for raw in data.get("nodes", []):
        node_id = raw.get("id") or raw.get("name")
        node_data = raw.get("data", {})
        label = node_data.get("label") or raw.get("label") or node_id
        hint = f"{node_data.get('name', '')} {node_data.get('category', '')} {label}"
        node_type, config = _infer_node_type(str(node_id), hint)
        graph.add_node(Node(id=str(node_id), type=node_type, label=str(label), config=config))
    for raw in data.get("edges", []):
        source = raw.get("source") or raw.get("sourceNode")
        target = raw.get("target") or raw.get("targetNode")
        if source and target and graph.has_node(str(source)) and graph.has_node(str(target)):
            graph.add_edge(str(source), str(target), label=str(raw.get("label", "")))
    return graph


def import_generic_json(data: dict[str, Any], name: str | None = None) -> AgentGraph:
    """Import AgentProof's own format, or any {"nodes": [...], "edges": [...]} JSON."""
    if data.get("nodes") and all("type" in n for n in data["nodes"]):
        try:
            return AgentGraph.from_dict(
                {"name": name or data.get("name", "Imported agent"), **data}
            )
        except (KeyError, ValueError):
            pass
    return import_flowise(data, name=name)


def import_agent(path: str | Path, name: str | None = None) -> AgentGraph:
    """Import an agent from a file, dispatching on extension and content."""
    path = Path(path)
    text = path.read_text()
    if path.suffix == ".py":
        return import_langgraph(text, name=name or path.stem)
    data = json.loads(text)
    return import_generic_json(data, name=name or data.get("name", path.stem))
