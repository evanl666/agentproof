"""AgentProof: test-first IDE for production agents.

Don't just build agents. Prove they behave.

Pipeline: Prompt -> Behavior Spec -> Tests -> Agent Graph -> Simulation
          -> Auto-fix -> Code Export -> CI
"""

__version__ = "0.1.0"

from agentproof.spec import BehaviorSpec, Constraint, ConstraintKind, parse_spec
from agentproof.graph import AgentGraph, Edge, Node, NodeType
from agentproof.synthesis import synthesize
from agentproof.scenarios import Scenario, ScenarioCategory, generate_scenarios
from agentproof.simulator import SimulationResult, Violation, run_suite, simulate
from agentproof.autofix import AutofixReport, autofix
from agentproof.coverage import CoverageReport, compute_coverage
from agentproof.diff import BehaviorDiff, behavior_diff
from agentproof.score import AgentScore, compute_score

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
]
