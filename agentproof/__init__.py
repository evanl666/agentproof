"""AgentProof: test-first IDE for production agents.

Don't just build agents. Prove they behave.

Pipeline: Prompt -> Behavior Spec -> Tests -> Agent Graph -> Simulation
          -> Auto-fix -> Code Export -> CI
"""

__version__ = "0.2.0"

from agentproof.spec import BehaviorSpec, Constraint, ConstraintKind, parse_spec
from agentproof.graph import AgentGraph, Edge, Node, NodeType
from agentproof.synthesis import synthesize
from agentproof.scenarios import Scenario, ScenarioCategory, generate_scenarios
from agentproof.simulator import SimulationResult, Violation, run_suite, simulate
from agentproof.autofix import AutofixReport, autofix
from agentproof.coverage import CoverageReport, compute_coverage
from agentproof.diff import BehaviorDiff, behavior_diff
from agentproof.score import AgentScore, compute_score
from agentproof.pricing import CostReport, compare_models, project_cost
from agentproof.packs import ScenarioPack, get_pack, list_packs
from agentproof.policy_lines import PolicyLine, compute_policy_lines, policy_summary
from agentproof.team import BehaviorHistory, ReviewRequest, review
from agentproof.export import export_agent

__all__ = [
    "BehaviorSpec",
    "Constraint",
    "ConstraintKind",
    "parse_spec",
    "AgentGraph",
    "Node",
    "Edge",
    "NodeType",
    "synthesize",
    "Scenario",
    "ScenarioCategory",
    "generate_scenarios",
    "simulate",
    "run_suite",
    "SimulationResult",
    "Violation",
    "autofix",
    "AutofixReport",
    "compute_coverage",
    "CoverageReport",
    "behavior_diff",
    "BehaviorDiff",
    "compute_score",
    "AgentScore",
    "project_cost",
    "compare_models",
    "CostReport",
    "get_pack",
    "list_packs",
    "ScenarioPack",
    "compute_policy_lines",
    "policy_summary",
    "PolicyLine",
    "BehaviorHistory",
    "ReviewRequest",
    "review",
    "export_agent",
]
