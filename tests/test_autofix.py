from agentproof.autofix import autofix
from agentproof.graph import NodeType
from agentproof.simulator import run_suite


def test_autofix_adds_all_four_repairs(fix_report):
    kinds = {f.kind for f in fix_report.fixes}
    assert kinds == {
        "policy_violation",
        "prompt_injection",
        "pii_egress",
        "unhandled_tool_error",
    }


def test_autofix_adds_enforcement_structure(fixed_graph):
    assert fixed_graph.nodes_of_type(NodeType.CONDITION)
    assert fixed_graph.nodes_of_type(NodeType.APPROVAL)
    guards = fixed_graph.nodes_of_type(NodeType.GUARD)
    guard_kinds = {g.config.get("kind") for g in guards}
    assert "injection_guard" in guard_kinds
    assert "pii_redaction" in guard_kinds
    assert fixed_graph.nodes_of_type(NodeType.FALLBACK)


def test_all_scenarios_pass_after_autofix(fixed_results):
    failing = [r for r in fixed_results if not r.passed]
    assert not failing, [
        (r.scenario.id, [v.message for v in r.violations]) for r in failing
    ]


def test_autofix_is_idempotent(fixed_graph, spec, scenarios):
    results = run_suite(fixed_graph, spec, scenarios)
    second = autofix(fixed_graph, spec, results)
    assert second.fixes == []


def test_autofix_does_not_mutate_original(naive_graph):
    assert not naive_graph.nodes_of_type(NodeType.GUARD)
    assert not naive_graph.nodes_of_type(NodeType.APPROVAL)


def test_approval_path_exercised_after_fix(fixed_results):
    assert any(r.approval_requested for r in fixed_results)
