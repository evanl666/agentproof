"""Adversarial invariants: degenerate inputs, determinism, roundtrips, and
graceful failure. These guard against crashes and silent-wrong behavior on
inputs the feature tests don't cover."""

import pytest

from agentproof.autofix import autofix
from agentproof.cli import main
from agentproof.coverage import compute_coverage
from agentproof.diff import behavior_diff
from agentproof.graph import AgentGraph
from agentproof.importers import import_generic_json, import_langgraph
from agentproof.pricing import project_cost
from agentproof.policy_lines import policy_summary
from agentproof.scenarios import Scenario, generate_scenarios
from agentproof.score import compute_score
from agentproof.simulator import SimulationResult, run_suite
from agentproof.spec import BehaviorSpec, parse_spec
from agentproof.studio import DEFAULT_SPEC
from agentproof.synthesis import synthesize


def _pipeline(spec_text, size=30, seed=42):
    spec = parse_spec(spec_text)
    graph = synthesize(spec)
    scenarios = generate_scenarios(spec, seed=seed, size=size)
    results = run_suite(graph, spec, scenarios)
    fixed = autofix(graph, spec, results).graph
    after = run_suite(fixed, spec, scenarios)
    score = compute_score(after, compute_coverage(fixed, after))
    return spec, graph, fixed, scenarios, after, score


DEGENERATE = [
    "",
    "   \n\t  \n",
    "# Just a title agent",
    "The agent should:\n- answer questions\n- be nice",
    "The agent must never:\n- send PII externally\n- ignore tool errors",
    "# 退款代理 🤖\nThe agent should:\n- 退款 under $50 automatically\n\nThe agent must never:\n- send PII externally",
    "The agent should refund under $9999999.99 automatically. Above $9999999.99 requires approval.",
    "The agent should refund under $0 automatically. Above $0 requires approval.",
    "Build a refund agent. Refunds under $50 are automatic. Refunds over $50 require approval. Never send PII externally.",
]


@pytest.mark.parametrize("text", DEGENERATE)
def test_pipeline_survives_degenerate_specs(text):
    spec, graph, fixed, scenarios, after, score = _pipeline(text)
    for v in (score.reliability, score.safety, score.cost_efficiency,
              score.coverage, score.autonomy, score.overall):
        assert 0 <= v <= 100


def test_pipeline_scale_extremes():
    for size in (1, 300):
        spec, graph, fixed, scenarios, after, score = _pipeline(DEFAULT_SPEC, size=size)
        assert len(after) == size


def test_full_determinism():
    a = [r.to_dict() for r in _pipeline(DEFAULT_SPEC)[4]]
    b = [r.to_dict() for r in _pipeline(DEFAULT_SPEC)[4]]
    assert a == b


def test_all_objects_roundtrip():
    spec, graph, fixed, scenarios, after, score = _pipeline(DEFAULT_SPEC)
    assert BehaviorSpec.from_dict(spec.to_dict()).to_dict() == spec.to_dict()
    assert AgentGraph.from_dict(fixed.to_dict()).to_dict() == fixed.to_dict()
    for s in scenarios:
        assert Scenario.from_dict(s.to_dict()).to_dict() == s.to_dict()
    for r in after:
        assert SimulationResult.from_dict(r.to_dict()).to_dict() == r.to_dict()


def test_autofix_is_idempotent_and_clean_agents_need_no_fixes():
    spec, graph, fixed, scenarios, after, score = _pipeline(DEFAULT_SPEC)
    assert autofix(fixed, spec, after).fixes == []
    # A pure Q&A agent declares no money/PII/injection/tool-error constraints,
    # so nothing should fire and no structural fix is needed.
    qa = parse_spec("The agent should answer product questions.")
    g = synthesize(qa)
    sc = generate_scenarios(qa)
    res = run_suite(g, qa, sc)
    assert all(r.passed for r in res)
    assert autofix(g, qa, res).fixes == []


def test_behavior_diff_of_identical_graphs_is_empty():
    spec, graph, fixed, scenarios, after, score = _pipeline(DEFAULT_SPEC)
    d = behavior_diff(spec, fixed, fixed, scenarios)
    assert d.newly_passing == [] and d.newly_failing == []
    assert d.risk_before == d.risk_after
    assert d.cost_delta_pct == 0.0


def test_importers_survive_hard_inputs():
    import_generic_json({"nodes": [], "edges": []})
    import_generic_json({"nodes": [{"id": "a", "type": "input", "label": "a"}],
                         "edges": [{"source": "a", "target": "ghost"}]})
    import_langgraph("from langgraph.graph import StateGraph\n"
                     "g=StateGraph(dict)\ng.add_node('x', lambda s:s)\ng.add_edge('x','x')\n")
    import_generic_json(
        {"nodes": [{"id": "a", "type": "llm", "label": "a"}, {"id": "b", "type": "tool", "label": "b"}],
         "edges": [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}]})
    import_generic_json({"foo": "bar"})  # not a graph at all


def test_imported_graph_simulates_regardless_of_entry_id():
    g = import_generic_json({
        "nodes": [
            {"name": "Trigger", "type": "n8n-nodes-base.manualTrigger"},
            {"name": "Charge", "type": "n8n-nodes-base.stripe"},
            {"name": "Email", "type": "n8n-nodes-base.gmail"},
        ],
        "connections": {"Trigger": {"main": [[{"node": "Charge"}]]},
                        "Charge": {"main": [[{"node": "Email"}]]}},
    })
    g.node("Charge").config["spend"] = True
    spec = parse_spec(DEFAULT_SPEC)
    res = run_suite(g, spec, generate_scenarios(spec, size=20))
    assert len(res) == 20


def test_policy_and_cost_edge_cases():
    qa = parse_spec("The agent should answer questions.")
    assert policy_summary(synthesize(qa), qa)["total"] == 0
    assert project_cost([], model_id="claude-sonnet-5").total_usd == 0


def test_cli_missing_project_is_graceful(capsys):
    assert main(["simulate", "/does/not/exist"]) == 2
    err = capsys.readouterr().err
    assert "no AgentProof project" in err
