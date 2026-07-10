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


def catalog() -> list[dict[str, Any]]:
    return MCP_CATALOG


def server(server_id: str) -> dict[str, Any]:
    for s in MCP_CATALOG:
        if s["id"] == server_id:
            return s
    raise KeyError(server_id)
