from agentproof.scenarios import ScenarioCategory


def _by_category(results, category):
    return [r for r in results if r.scenario.category == category]


def test_normal_traffic_passes_on_naive_graph(naive_results):
    normal = _by_category(naive_results, ScenarioCategory.NORMAL)
    assert normal and all(r.passed for r in normal)


def test_adversarial_refunds_fail_on_naive_graph(naive_results):
    adversarial = _by_category(naive_results, ScenarioCategory.ADVERSARIAL)
    assert adversarial and all(not r.passed for r in adversarial)
    kinds = {v.kind for r in adversarial for v in r.violations}
    assert "policy_violation" in kinds


def test_prompt_injection_compromises_naive_graph(naive_results):
    injections = _by_category(naive_results, ScenarioCategory.PROMPT_INJECTION)
    assert injections and all(not r.passed for r in injections)
    kinds = {v.kind for r in injections for v in r.violations}
    assert "prompt_injection" in kinds


def test_pii_leaks_on_naive_graph(naive_results):
    leaks = _by_category(naive_results, ScenarioCategory.PII_LEAK)
    assert leaks and all(not r.passed for r in leaks)
    kinds = {v.kind for r in leaks for v in r.violations}
    assert kinds == {"pii_egress"}


def test_tool_failures_unhandled_on_naive_graph(naive_results):
    failures = _by_category(naive_results, ScenarioCategory.TOOL_FAILURE)
    assert failures and all(not r.passed for r in failures)


def test_boundary_below_limit_passes_above_fails(naive_results):
    boundary = _by_category(naive_results, ScenarioCategory.BOUNDARY)
    below = [r for r in boundary if r.scenario.amount is not None and r.scenario.amount <= 50]
    above = [r for r in boundary if r.scenario.amount is not None and r.scenario.amount > 50]
    assert below and all(r.passed for r in below)
    assert above and all(not r.passed for r in above)


def test_traces_record_visited_nodes_and_edges(naive_results):
    result = naive_results[0]
    assert result.visited_nodes[0] == "input"
    assert result.visited_edges
    assert result.cost_tokens > 0


def test_simulation_is_deterministic(naive_graph, spec, scenarios):
    from agentproof.simulator import run_suite

    a = run_suite(naive_graph, spec, scenarios)
    b = run_suite(naive_graph, spec, scenarios)
    assert [r.to_dict() for r in a] == [r.to_dict() for r in b]


def test_read_and_export_tool_is_fixable(spec, scenarios):
    """A tool that both loads PII and egresses it (an 'export results' tool) must
    still be protected by an upstream redaction guard — the structural fix, not the
    within-node load order, is what counts. Regression for the SQL-agent case."""
    from agentproof.autofix import autofix
    from agentproof.graph import AgentGraph, Edge, Node, NodeType
    from agentproof.simulator import run_suite

    # planner -> export (returns_pii AND external) -> output
    g = AgentGraph(name="exporter", nodes=[
        Node(id="input", type=NodeType.INPUT, label="in"),
        Node(id="planner", type=NodeType.LLM, label="planner"),
        Node(id="export_results", type=NodeType.TOOL, label="Export results",
             config={"returns_pii": True, "external": True}),
        Node(id="responder", type=NodeType.LLM, label="responder"),
        Node(id="output", type=NodeType.OUTPUT, label="out"),
    ], edges=[
        Edge("input", "planner"), Edge("planner", "export_results"),
        Edge("export_results", "responder"), Edge("responder", "output"),
    ])
    before = run_suite(g, spec, scenarios)
    pii = [r for r in before if r.scenario.category == ScenarioCategory.PII_LEAK]
    assert pii and any(not r.passed for r in pii), "unguarded read-and-export should leak"

    fixed = autofix(g, spec, before).graph
    after = run_suite(fixed, spec, scenarios)
    pii_after = [r for r in after if r.scenario.category == ScenarioCategory.PII_LEAK]
    assert all(r.passed for r in pii_after), "a redaction guard must fix the export leak"
