"""AgentProof: test-first IDE for production agents.

Don't just build agents. Prove they behave.

Pipeline: Prompt -> Behavior Spec -> Tests -> Agent Graph -> Simulation
          -> Auto-fix -> Code Export -> CI
"""

__version__ = "0.12.0"

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
from agentproof.badge import render_badge, score_badge, write_badge
from agentproof.importers import (
    detect_format,
    import_agent,
    import_dify,
    import_n8n,
    import_openai_builder,
    import_python_agent,
)
from agentproof.runtime import (
    AgentRuntime,
    ClaudePlanner,
    LocalPlanner,
    RunResult,
    default_planner,
)
from agentproof.proofs import Proof, all_hold, proof_summary, prove
from agentproof.replay import extract_messages, traces_to_scenarios
from agentproof.plugins import ContentPolicyPlugin, register_plugin, registered_plugins
from agentproof.middleware import GuardMiddleware, export_middleware
from agentproof.redteam import ClaudeRedTeam, TemplateRedTeam, redteam_scenarios
from agentproof.playground import build_playground_html, write_playground
from agentproof.probe import http_agent, probe_agent, probe_summary, detect_violations
from agentproof.agentworld import AgentWorld, Effect
from agentproof.infer import infer_spec, infer_from_graph, analyze_risk
from agentproof.safetools import compile_openapi, compile_to_repo
from agentproof.proof_movie import build_proof_movie_html, write_proof_movie
from agentproof.risk import RiskCategory, classify_action, infer_tool_risk, is_sensitive
from agentproof.smart import SmartSpecParser, SmartJudge, smart_parse_spec
from agentproof.intelligence import (
    SmartScenarioGen,
    SmartSynthesizer,
    smart_generate_scenarios,
    smart_synthesize,
    use_llm,
)
from agentproof.coverage2 import RiskCoverageReport, compute_risk_coverage
from agentproof.mutation import MutationReport, mutation_test
from agentproof.prioritize import prioritize, risk_weight, top_scenarios
from agentproof.incident import incidents_to_regressions, regression_pr_body
from agentproof.marketplace import install_pack, list_registry, publish_pack, search_packs
from agentproof.transaction import check_transaction_contracts
from agentproof.delegation import SubAgent, check_delegation
from agentproof.prbot import build_review_comment
from agentproof.compliance import compliance_data, write_compliance
from agentproof.attack import (
    AdaptiveAttacker,
    AttackTranscript,
    TemplateAttacker,
    attack_goals,
    http_multiturn_agent,
    make_attacker,
    run_campaign,
    runtime_agent,
)
from agentproof.audit import AuditReport, audit_agent, audit_spec, write_audit

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
    "score_badge",
    "render_badge",
    "write_badge",
    "detect_format",
    "import_agent",
    "import_n8n",
    "import_dify",
    "import_openai_builder",
    "import_python_agent",
    "AgentRuntime",
    "LocalPlanner",
    "ClaudePlanner",
    "RunResult",
    "default_planner",
    "prove",
    "Proof",
    "all_hold",
    "proof_summary",
    "traces_to_scenarios",
    "extract_messages",
    "ContentPolicyPlugin",
    "register_plugin",
    "registered_plugins",
    "GuardMiddleware",
    "export_middleware",
    "redteam_scenarios",
    "ClaudeRedTeam",
    "TemplateRedTeam",
    "write_playground",
    "build_playground_html",
    "probe_agent",
    "http_agent",
    "probe_summary",
    "detect_violations",
    "AgentWorld",
    "Effect",
    "infer_spec",
    "infer_from_graph",
    "analyze_risk",
    "compile_openapi",
    "compile_to_repo",
    "build_proof_movie_html",
    "write_proof_movie",
    "RiskCategory",
    "classify_action",
    "infer_tool_risk",
    "is_sensitive",
    "smart_parse_spec",
    "SmartSpecParser",
    "SmartJudge",
    "smart_synthesize",
    "smart_generate_scenarios",
    "SmartSynthesizer",
    "SmartScenarioGen",
    "use_llm",
    "compute_risk_coverage",
    "RiskCoverageReport",
    "mutation_test",
    "MutationReport",
    "prioritize",
    "top_scenarios",
    "risk_weight",
    "incidents_to_regressions",
    "regression_pr_body",
    "publish_pack",
    "install_pack",
    "search_packs",
    "list_registry",
    "check_transaction_contracts",
    "check_delegation",
    "SubAgent",
    "build_review_comment",
    "compliance_data",
    "write_compliance",
    "AdaptiveAttacker",
    "TemplateAttacker",
    "AttackTranscript",
    "attack_goals",
    "run_campaign",
    "make_attacker",
    "http_multiturn_agent",
    "runtime_agent",
    "audit_agent",
    "audit_spec",
    "AuditReport",
    "write_audit",
]
