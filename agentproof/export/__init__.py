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


def export_agent(target, spec, graph, scenarios, out_dir):
    """Export a verified agent to one of the supported frameworks."""
    if target not in EXPORTERS:
        raise KeyError(f"Unknown target {target!r}; available: {', '.join(EXPORTERS)}")
    return EXPORTERS[target](spec, graph, scenarios, out_dir)


__all__ = [
    "export_langgraph",
    "export_openai_agents",
    "export_crewai",
    "export_typescript",
    "export_agent",
    "EXPORTERS",
]
