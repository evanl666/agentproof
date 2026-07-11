"""Tool-stub generation — give each exported tool a fleshed-out scaffold.

A one-line `raise NotImplementedError` tells you nothing about *how* to wire the
tool. This module renders a real scaffold per tool: typed inputs pulled from
state, a docstring with Args/Returns, an example return shape, and a clearly
marked ``# TODO`` pointing at where to call your system (a payments API, your DB,
an HTTP endpoint, or an MCP tool server). Deterministic by default — infers the
shape from the tool's risk flags — and, when a model key is present, the LLM
writes an idiomatic body specific to each tool. Money-moving and high-risk tools
still end in NotImplementedError so an unwired tool can never silently "succeed".
"""

from __future__ import annotations

from agentproof.graph import AgentGraph, Node, NodeType

_HEADER = [
    '"""Tool implementations. Each function receives the run state and returns a',
    'partial state update (only the keys it changes). Wire the TODO in each body to',
    'your real system — a payments API, your database, an HTTP service, or an MCP',
    'tool server. The safety policy is enforced around these by the agent graph."""',
    "",
    "import time",
    "from functools import wraps",
    "",
    "",
    "def with_retry(max_attempts: int = 3, backoff_seconds: float = 2.0):",
    "    def decorator(fn):",
    "        @wraps(fn)",
    "        def wrapper(*args, **kwargs):",
    "            last_error = None",
    "            for attempt in range(max_attempts):",
    "                try:",
    "                    return fn(*args, **kwargs)",
    "                except Exception as exc:  # noqa: BLE001",
    "                    last_error = exc",
    "                    time.sleep(backoff_seconds * (attempt + 1))",
    "            raise last_error",
    "        return wrapper",
    "    return decorator",
    "",
    "",
]


def _kind(node: Node) -> str:
    c = node.config
    if c.get("spend"):
        return "money"
    if c.get("external"):
        return "external"
    if c.get("returns_pii") or c.get("sensitive"):
        return "lookup"
    if c.get("high_risk"):
        return "high_risk"
    return "generic"


# kind -> (typed reads, example return, TODO example, whether to raise)
_SHAPES = {
    "money": (
        [("customer_id", "str"), ("amount", "float")],
        '{"transaction": {"id": "txn_123", "status": "completed", "amount": amount}}',
        "call your payments provider, e.g. stripe.Refund.create("
        "customer=customer_id, amount=int(amount * 100))",
        True,
    ),
    "lookup": (
        [("customer_id", "str")],
        '{"customer": {"id": customer_id, "name": "[REDACTED]", "email": "[REDACTED]"}}',
        "query your database or CRM, e.g. db.customers.find_one({'id': customer_id})",
        True,
    ),
    "external": (
        [("recipient", "str"), ("body", "str")],
        '{"delivery": {"to": recipient, "status": "sent"}}',
        "send via your provider, e.g. sendgrid.send(to=recipient, body=body) — the "
        "graph redacts PII before this runs",
        True,
    ),
    "high_risk": (
        [("target", "str")],
        '{"result": {"target": target, "status": "done"}}',
        "perform the action — the graph gates it behind human approval first",
        True,
    ),
    "generic": (
        [],
        '{"result": {"status": "ok"}}',
        "call your real system or an MCP tool here",
        True,
    ),
}


def render_tool_body(node: Node) -> list[str]:
    reads, example_return, todo, raise_stub = _SHAPES[_kind(node)]
    binding = node.config.get("binding") or {}
    retry = node.config.get("retry")
    lines: list[str] = []
    if retry:
        lines.append(
            f"@with_retry(max_attempts={retry.get('max_attempts', 3)}, "
            f"backoff_seconds={retry.get('backoff_seconds', 2)})"
        )
    lines.append(f"def {node.id}(state: dict) -> dict:")

    # MCP-bound (from the Connect step): call the mapped server/tool via the client.
    if binding.get("type") == "mcp":
        server, mtool = binding.get("server", ""), binding.get("tool", "")
        doc = [f'    """{node.label}. Powered by the {server!r} MCP server → "{mtool}".']
        if reads:
            doc.append("")
            doc.append("    Reads from state:")
            doc += [f"        {name} ({typ})" for name, typ in reads]
        doc.append('    """')
        lines += doc
        lines.append(f"    return _mcp_call({server!r}, {mtool!r}, state)")
        lines += ["", ""]
        return lines

    doc = [f'    """{node.label}.', ""]
    if reads:
        doc.append("    Reads from state:")
        doc += [f"        {name} ({typ})" for name, typ in reads]
    doc.append(f"    Returns (partial state update): {example_return}")
    doc.append('    """')
    lines += doc
    for name, typ in reads:
        caster = {"float": "float", "int": "int"}.get(typ, "")
        # Null-safe so an unwired stub reaches its NotImplementedError, not a TypeError.
        expr = f"{caster}(state.get({name!r}) or 0)" if caster else f"state.get({name!r})"
        lines.append(f"    {name}: {typ} = {expr}")
    lines.append(f"    # TODO: {todo}")
    # A tool the user bound to "your own function" gets a plain, fill-in scaffold;
    # everything else raises until wired so it can't silently succeed.
    if binding.get("type") == "function":
        lines.append(f"    # ↑ Connect step: implement {node.id} here and return the update above.")
        lines.append(f"    return {example_return}")
    elif raise_stub:
        lines.append(f"    raise NotImplementedError({('Wire ' + node.id + ' to your real system')!r})")
    else:
        lines.append(f"    return {example_return}")
    lines += ["", ""]
    return lines


def _mcp_helper(graph: AgentGraph) -> list[str]:
    """The _mcp_call shim + a manifest of which servers/creds the agent needs."""
    servers: dict[str, set] = {}
    for n in graph.nodes_of_type(NodeType.TOOL):
        b = n.config.get("binding") or {}
        if b.get("type") == "mcp" and b.get("server"):
            servers.setdefault(b["server"], set())
    if not servers:
        return []
    from agentproof.mcp_catalog import server_env

    manifest = {s: server_env(s) for s in servers}
    return [
        "",
        "# ── MCP wiring (from the Connect step) ────────────────────────────────",
        "# This agent's tools are bound to these MCP servers. Set the env vars, then",
        "# wire `_mcp_call` to your MCP client (e.g. the `mcp` Python SDK's stdio or",
        "# SSE client). Until then, calls raise so an unconnected tool can't succeed.",
        f"MCP_SERVERS = {manifest!r}",
        "",
        "",
        "def _mcp_call(server: str, tool: str, state: dict) -> dict:",
        '    """Invoke `tool` on the named MCP `server` with the run state as args."""',
        "    # TODO: connect your MCP client, e.g.:",
        "    #   from mcp import ClientSession, StdioServerParameters",
        "    #   from mcp.client.stdio import stdio_client",
        "    #   async with stdio_client(SERVER_PARAMS[server]) as (r, w):",
        "    #       async with ClientSession(r, w) as s:",
        "    #           res = await s.call_tool(tool, arguments=state)",
        "    #   return res.structuredContent or {}",
        "    raise NotImplementedError(",
        "        f'Connect the {server!r} MCP server (creds: {MCP_SERVERS.get(server)}) "
        "and call {tool!r}'",
        "    )",
        "",
        "",
    ]


def render_tools_module(graph: AgentGraph, spec=None, model: str | None = None) -> str:
    """Render tools.py. LLM-written bodies when a key is present, else rich scaffolds.

    If any tool was bound in the Connect step, use the deterministic renderer so
    those bindings (MCP calls / function scaffolds) are honored exactly."""
    has_bindings = any(n.config.get("binding") for n in graph.nodes_of_type(NodeType.TOOL))
    if spec is not None and not has_bindings:
        from agentproof.intelligence import use_llm

        if use_llm(model):
            try:
                return _llm_tools_module(graph, spec, model or "claude-haiku-4-5")
            except Exception:  # noqa: BLE001 — deterministic scaffold fallback
                pass
    lines = list(_HEADER)
    lines += _mcp_helper(graph)  # _mcp_call shim, only when a tool is MCP-bound
    for node in graph.nodes_of_type(NodeType.TOOL):
        lines += render_tool_body(node)
    return "\n".join(lines)


_LLM_SYSTEM = """You write tools.py for an AI agent. Each function takes `state: dict`
and returns a partial state update (only the keys it changes). For every tool:
- a precise docstring with the state keys it reads and the shape it returns,
- typed extraction of those keys from `state`,
- a clearly marked `# TODO:` line naming the concrete real system or MCP tool to
  call (be specific to the tool's purpose),
- end MONEY-MOVING and HIGH-RISK tools with `raise NotImplementedError(...)` so an
  unwired tool can't silently succeed; other tools may return an example dict.
Include the `with_retry` decorator helper at the top. Output ONLY valid Python for
tools.py — no markdown fences, no prose."""


def _llm_tools_module(graph: AgentGraph, spec, model: str) -> str:
    import anthropic

    manifest = []
    for n in graph.nodes_of_type(NodeType.TOOL):
        flags = [k for k in ("spend", "high_risk", "external", "returns_pii", "sensitive")
                 if n.config.get(k)]
        manifest.append(f"- {n.id} ({n.label}): {', '.join(flags) or 'no special risk'}")
    ctx = (
        f"Agent: {spec.name}\nWrite tools.py for these tools "
        f"(function name = id):\n" + "\n".join(manifest)
    )
    resp = anthropic.Anthropic().messages.create(
        model=model, max_tokens=2000, system=_LLM_SYSTEM,
        messages=[{"role": "user", "content": ctx}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    if "```" in text:
        parts = text.split("```")
        text = max(parts, key=len)
        if text.lstrip().startswith(("python", "py")):
            text = text.split("\n", 1)[1] if "\n" in text else text
    text = text.strip()
    # Guard: the model must have produced a module; else fall back.
    if "def " not in text:
        raise ValueError("LLM did not return tool functions")
    return text + "\n"
