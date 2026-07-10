from agentproof.export.langgraph import export_langgraph
from agentproof.export.targets import (
    export_crewai,
    export_openai_agents,
    export_typescript,
)

# Registry: target id -> exporter(spec, graph, scenarios, out_dir) -> list[Path]
EXPORTERS = {
    "langgraph": export_langgraph,
    "openai": export_openai_agents,
    "crewai": export_crewai,
    "typescript": export_typescript,
}


def export_agent(target, spec, graph, scenarios, out_dir, model=None):
    """Export a verified agent to a framework.

    Known frameworks (langgraph/openai/crewai/typescript) use a deterministic
    exporter. ANY other framework name (langchain, autogen, pydantic-ai, agno,
    google-adk, …) is handled by the flexible LLM-assembled exporter — the
    verified policy core is deterministic, the framework glue is model-written,
    with an offline scaffold fallback."""
    if target in EXPORTERS:
        return EXPORTERS[target](spec, graph, scenarios, out_dir)
    from agentproof.export.smart_export import export_framework

    return export_framework(target, spec, graph, scenarios, out_dir, model=model)


__all__ = [
    "export_langgraph",
    "export_openai_agents",
    "export_crewai",
    "export_typescript",
    "export_agent",
    "EXPORTERS",
]
