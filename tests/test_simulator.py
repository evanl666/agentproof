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
