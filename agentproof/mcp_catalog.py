"""A curated catalog of popular open-source MCP tool servers.

Users shouldn't have to hand-name every tool. This catalog lets them attach
known tools from the Model Context Protocol ecosystem (GitHub, Stripe, Postgres,
Slack, filesystem, web search, …) with the *right risk flags already set* — so a
"Merge pull request" or "Process payment" comes in pre-marked high-risk / money
and the pipeline guards it automatically. Each entry mirrors the real server's
headline tools; users can still add fully custom tools too.

Risk flags map to the same graph config the rest of the engine uses:
  money -> spend, high_risk -> high_risk, external -> external, pii -> returns_pii
"""

from __future__ import annotations

from typing import Any

# server -> tools, each {label, risk:{money?,high_risk?,external?,pii?}}
MCP_CATALOG: list[dict[str, Any]] = [
    {
        "id": "github", "name": "GitHub", "icon": "🐙",
        "desc": "Repositories, issues, and pull requests",
        "package": "@modelcontextprotocol/server-github",
        "tools": [
            {"label": "Search repositories", "risk": {}},
            {"label": "Read file contents", "risk": {"pii": True}},
            {"label": "Create issue", "risk": {"external": True}},
            {"label": "Merge pull request", "risk": {"high_risk": True}},
            {"label": "Delete repository", "risk": {"high_risk": True}},
        ],
    },
    {
        "id": "stripe", "name": "Stripe", "icon": "💳",
        "desc": "Payments, refunds, and customers",
        "package": "@stripe/mcp",
        "tools": [
            {"label": "Look up customer", "risk": {"pii": True}},
            {"label": "Create payment", "risk": {"money": True}},
            {"label": "Issue refund", "risk": {"money": True}},
            {"label": "Cancel subscription", "risk": {"high_risk": True}},
        ],
    },
    {
        "id": "postgres", "name": "Postgres", "icon": "🐘",
        "desc": "Query and inspect a SQL database",
        "package": "@modelcontextprotocol/server-postgres",
        "tools": [
            {"label": "Run read-only query", "risk": {"pii": True}},
            {"label": "Describe table schema", "risk": {}},
            {"label": "Execute write statement", "risk": {"high_risk": True}},
        ],
    },
    {
        "id": "slack", "name": "Slack", "icon": "💬",
        "desc": "Post messages and read channels",
        "package": "@modelcontextprotocol/server-slack",
        "tools": [
            {"label": "List channels", "risk": {}},
            {"label": "Read channel messages", "risk": {"pii": True}},
            {"label": "Post message", "risk": {"external": True}},
        ],
    },
    {
        "id": "filesystem", "name": "Filesystem", "icon": "📁",
        "desc": "Read and write local files",
        "package": "@modelcontextprotocol/server-filesystem",
        "tools": [
            {"label": "Read file", "risk": {"pii": True}},
            {"label": "List directory", "risk": {}},
            {"label": "Write file", "risk": {"high_risk": True}},
            {"label": "Delete file", "risk": {"high_risk": True}},
        ],
    },
    {
        "id": "web-search", "name": "Web Search", "icon": "🔎",
        "desc": "Search the web and fetch pages",
        "package": "@modelcontextprotocol/server-brave-search",
        "tools": [
            {"label": "Web search", "risk": {}},
            {"label": "Fetch URL", "risk": {"external": True}},
        ],
    },
    {
        "id": "gdrive", "name": "Google Drive", "icon": "📄",
        "desc": "Search and read documents",
        "package": "@modelcontextprotocol/server-gdrive",
        "tools": [
            {"label": "Search documents", "risk": {"pii": True}},
            {"label": "Read document", "risk": {"pii": True}},
        ],
    },
    {
        "id": "email", "name": "Email (SMTP)", "icon": "✉️",
        "desc": "Send transactional email",
        "package": "mcp-server-email",
        "tools": [
            {"label": "Send email", "risk": {"external": True}},
        ],
    },
]


# Credentials each server needs at connect time (surfaced in the Connect step).
_SERVER_ENV = {
    "github": ["GITHUB_TOKEN"],
    "stripe": ["STRIPE_API_KEY"],
    "postgres": ["DATABASE_URL"],
    "slack": ["SLACK_BOT_TOKEN"],
    "filesystem": [],
    "web-search": ["BRAVE_API_KEY"],
    "gdrive": ["GOOGLE_OAUTH_TOKEN"],
    "email": ["SMTP_URL"],
}

_STOPWORDS = {"the", "a", "an", "to", "of", "and", "for", "on", "in", "with",
              "results", "result", "data", "information", "info", "your", "my", "any"}


def server_env(server_id: str) -> list[str]:
    return _SERVER_ENV.get(server_id, [])


def catalog() -> list[dict[str, Any]]:
    return MCP_CATALOG


def server(server_id: str) -> dict[str, Any]:
    for s in MCP_CATALOG:
        if s["id"] == server_id:
            return s
    raise KeyError(server_id)


def _tokens(text: str) -> set[str]:
    import re

    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _risk_bonus(tool_risk: dict, mcp_risk: dict) -> int:
    bonus = 0
    for flag, pts in (("money", 3), ("high_risk", 2), ("external", 1), ("pii", 1)):
        if tool_risk.get(flag) and mcp_risk.get(flag):
            bonus += pts
    return bonus


def suggest_bindings(tool_label: str, tool_risk: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """Rank MCP (server, tool) pairs that could power a given behavioral tool.

    Matches on label-token overlap plus a shared-risk bonus, so "Process refund"
    (money) surfaces Stripe's "Issue refund" and a data-lookup tool surfaces
    Postgres. Returns the best few candidates; the UI also offers function/stub.
    """
    ltokens = _tokens(tool_label)
    out: list[dict[str, Any]] = []
    for srv in MCP_CATALOG:
        for t in srv["tools"]:
            overlap = len(ltokens & _tokens(t["label"]))
            score = overlap * 3 + _risk_bonus(tool_risk, t.get("risk", {}))
            if score <= 0:
                continue
            out.append({
                "type": "mcp", "server": srv["id"], "server_name": srv["name"],
                "icon": srv.get("icon", "🔌"), "tool": t["label"], "score": score,
                "env": server_env(srv["id"]),
            })
    out.sort(key=lambda c: -c["score"])
    return out[:limit]
