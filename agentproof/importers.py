"""Import existing agents into AgentProof.

You don't have to build your agent here to prove it here. Importers lift
agents written elsewhere into the AgentProof graph model so they can be
simulated, scored, auto-fixed and re-exported:

- LangGraph Python source (static AST analysis of add_node/add_edge calls)
- Flowise chatflow JSON exports
- n8n workflow JSON (edges live in the `connections` map)
- Dify DSL (JSON or YAML; `workflow.graph.{nodes,edges}`)
- OpenAI Agent Builder / ReactFlow-style workflow JSON
- Generic node/edge JSON (AgentProof's own format)

`import_agent(path)` sniffs the format from the file's shape, so callers rarely
need to pick an importer by hand.
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


# Dify's node data.type -> AgentProof node type. Dify names the workflow steps
# explicitly, so we trust these over keyword inference.
_DIFY_TYPES = {
    "start": (NodeType.INPUT, {}),
    "end": (NodeType.OUTPUT, {}),
    "answer": (NodeType.OUTPUT, {}),
    "llm": (NodeType.LLM, {"model": "imported"}),
    "agent": (NodeType.LLM, {"model": "imported"}),
    "question-classifier": (NodeType.LLM, {"model": "imported"}),
    "if-else": (NodeType.CONDITION, {}),
    "code": (NodeType.TOOL, {}),
    "http-request": (NodeType.TOOL, {"external": True}),
    "tool": (NodeType.TOOL, {}),
    "knowledge-retrieval": (NodeType.TOOL, {"datasource": "kb", "returns_pii": True}),
    "template-transform": (NodeType.TOOL, {}),
    "variable-assigner": (NodeType.TOOL, {}),
}

# OpenAI Agent Builder node "type" -> AgentProof node type.
_OPENAI_BUILDER_TYPES = {
    "start": (NodeType.INPUT, {}),
    "input": (NodeType.INPUT, {}),
    "end": (NodeType.OUTPUT, {}),
    "output": (NodeType.OUTPUT, {}),
    "agent": (NodeType.LLM, {"model": "imported"}),
    "llm": (NodeType.LLM, {"model": "imported"}),
    "guardrail": (NodeType.GUARD, {"kind": "injection_guard"}),
    "approval": (NodeType.APPROVAL, {}),
    "human": (NodeType.APPROVAL, {}),
    "if": (NodeType.CONDITION, {}),
    "condition": (NodeType.CONDITION, {}),
    "logic": (NodeType.CONDITION, {}),
    "tool": (NodeType.TOOL, {}),
    "function": (NodeType.TOOL, {}),
    "mcp": (NodeType.TOOL, {}),
}


def import_n8n(data: dict[str, Any], name: str | None = None) -> AgentGraph:
    """Import an n8n workflow: nodes are a list, edges live in `connections`.

    n8n keys connections by the source node's *name* and lists targets under
    `main[<output index>][<i>].node`.
    """
    graph = AgentGraph(name=name or data.get("name", "Imported n8n workflow"))
    id_by_name: dict[str, str] = {}
    for raw in data.get("nodes", []):
        node_name = str(raw.get("name") or raw.get("id"))
        node_id = node_name
        n8n_type = str(raw.get("type", ""))  # e.g. "n8n-nodes-base.httpRequest"
        short = n8n_type.rsplit(".", 1)[-1]
        node_type, config = _infer_node_type(short, f"{node_name} {short}")
        id_by_name[node_name] = node_id
        graph.add_node(Node(id=node_id, type=node_type, label=node_name, config=config))
    for source_name, outputs in (data.get("connections") or {}).items():
        for _output_kind, branches in outputs.items():
            for branch in branches:
                for conn in branch or []:
                    target_name = conn.get("node")
                    if source_name in id_by_name and target_name in id_by_name:
                        graph.add_edge(id_by_name[source_name], id_by_name[target_name])
    _ensure_endpoints(graph)
    return graph


def import_dify(data: dict[str, Any], name: str | None = None) -> AgentGraph:
    """Import a Dify DSL app (workflow.graph.{nodes,edges}, or a bare graph)."""
    graph_data = (
        data.get("workflow", {}).get("graph")
        or data.get("graph")
        or data
    )
    app_name = name or data.get("app", {}).get("name") or data.get("name", "Imported Dify app")
    graph = AgentGraph(name=app_name)
    for raw in graph_data.get("nodes", []):
        node_id = str(raw.get("id"))
        node_data = raw.get("data", {})
        dify_type = str(node_data.get("type", "")).lower()
        label = node_data.get("title") or node_data.get("label") or dify_type or node_id
        if dify_type in _DIFY_TYPES:
            node_type, config = _DIFY_TYPES[dify_type]
            config = dict(config)
        else:
            node_type, config = _infer_node_type(str(label), dify_type)
        graph.add_node(Node(id=node_id, type=node_type, label=str(label), config=config))
    for raw in graph_data.get("edges", []):
        source, target = str(raw.get("source")), str(raw.get("target"))
        if graph.has_node(source) and graph.has_node(target):
            graph.add_edge(source, target)
    _ensure_endpoints(graph)
    return graph


def import_openai_builder(data: dict[str, Any], name: str | None = None) -> AgentGraph:
    """Import an OpenAI Agent Builder / ReactFlow-style workflow JSON."""
    graph = AgentGraph(name=name or data.get("name", "Imported OpenAI Agent Builder workflow"))
    for raw in data.get("nodes", []):
        node_id = str(raw.get("id"))
        node_data = raw.get("data", {})
        raw_type = str(raw.get("type") or node_data.get("type", "")).lower()
        label = node_data.get("label") or node_data.get("name") or raw_type or node_id
        if raw_type in _OPENAI_BUILDER_TYPES:
            node_type, config = _OPENAI_BUILDER_TYPES[raw_type]
            config = dict(config)
        else:
            node_type, config = _infer_node_type(str(label), raw_type)
        graph.add_node(Node(id=node_id, type=node_type, label=str(label), config=config))
    for raw in data.get("edges", []):
        source = str(raw.get("source") or raw.get("from"))
        target = str(raw.get("target") or raw.get("to"))
        if graph.has_node(source) and graph.has_node(target):
            graph.add_edge(source, target, label=str(raw.get("label", "")))
    _ensure_endpoints(graph)
    return graph


def _ensure_endpoints(graph: AgentGraph) -> None:
    """Guarantee at least one input node so simulation has an entry point."""
    if not graph.nodes_of_type(NodeType.INPUT):
        targets = {e.target for e in graph.edges}
        for node in graph.nodes:
            if node.id not in targets and node.type != NodeType.OUTPUT:
                node.type = NodeType.INPUT
                break


def detect_format(data: dict[str, Any]) -> str:
    """Sniff which JSON dialect a workflow export is written in."""
    if "connections" in data and isinstance(data.get("connections"), dict):
        return "n8n"
    if "workflow" in data or ("graph" in data and isinstance(data["graph"], dict)):
        return "dify"
    nodes = data.get("nodes") or []
    if nodes and isinstance(nodes[0], dict):
        top_types = {str(n.get("type", "")).lower() for n in nodes if n.get("type")}
        data_types = {
            str(n.get("data", {}).get("type", "")).lower()
            for n in nodes
            if n.get("data", {}).get("type")
        }
        # AgentProof-native: every node carries an explicit AgentProof NodeType
        # (checked first so it isn't shadowed by the shared "llm" keyword).
        native_values = {t.value for t in NodeType}
        if (
            all("type" in n for n in nodes)
            and not data_types
            and top_types
            and top_types <= native_values
        ):
            return "native"
        if data_types & set(_DIFY_TYPES):
            return "dify"
        if "guardrail" in top_types or (top_types & set(_OPENAI_BUILDER_TYPES)):
            return "openai_builder"
    return "flowise"


def import_generic_json(data: dict[str, Any], name: str | None = None) -> AgentGraph:
    """Import any supported JSON workflow, sniffing the dialect from its shape."""
    fmt = detect_format(data)
    if fmt == "n8n":
        return import_n8n(data, name=name)
    if fmt == "dify":
        return import_dify(data, name=name)
    if fmt == "openai_builder":
        return import_openai_builder(data, name=name)
    if fmt == "native":
        try:
            return AgentGraph.from_dict(
                {"name": name or data.get("name", "Imported agent"), **data}
            )
        except (KeyError, ValueError):
            pass
    return import_flowise(data, name=name)


def _load_structured(path: Path) -> dict[str, Any]:
    """Parse JSON, or YAML when the file is YAML and PyYAML is available."""
    text = path.read_text()
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # optional dependency

            return yaml.safe_load(text)
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "Importing Dify YAML requires PyYAML (`pip install pyyaml`), "
                "or export the app as JSON."
            ) from exc
    return json.loads(text)


def import_agent(path: str | Path, name: str | None = None) -> AgentGraph:
    """Import an agent from a file, dispatching on extension and content."""
    path = Path(path)
    if path.suffix == ".py":
        return import_langgraph(path.read_text(), name=name or path.stem)
    data = _load_structured(path)
    return import_generic_json(data, name=name or data.get("name", path.stem))
