from agentproof.coverage import compute_coverage
from agentproof.diff import behavior_diff
from agentproof.score import compute_score


def test_coverage_bounds(fixed_graph, fixed_results):
    coverage = compute_coverage(fixed_graph, fixed_results)
    assert 0 < coverage.node_coverage <= 1
    assert 0 < coverage.edge_coverage <= 1
    assert 0 < coverage.overall <= 1


def test_coverage_reports_unvisited_structure(fixed_graph, fixed_results):
    coverage = compute_coverage(fixed_graph, fixed_results)
    all_ids = {n.id for n in fixed_graph.nodes}
    assert set(coverage.visited_nodes) <= all_ids
    assert set(coverage.unvisited_nodes) <= all_ids
    assert set(coverage.visited_nodes) | set(coverage.unvisited_nodes) == all_ids
    assert coverage.approval_paths_tested


def test_naive_agent_is_not_shippable(naive_graph, naive_results):
    coverage = compute_coverage(naive_graph, naive_results)
    score = compute_score(naive_results, coverage)
    assert not score.shippable
    assert score.safety < 50


def test_fixed_agent_is_shippable(fixed_graph, fixed_results):
    coverage = compute_coverage(fixed_graph, fixed_results)
    score = compute_score(fixed_results, coverage)
    assert score.safety == 100
    assert score.reliability == 100
    assert score.shippable


def test_behavior_diff_shows_risk_reduction(spec, naive_graph, fixed_graph, scenarios):
    diff = behavior_diff(spec, naive_graph, fixed_graph, scenarios)
    assert diff.risk_before > 0
    assert diff.risk_after == 0
    assert diff.newly_passing
    assert not diff.newly_failing
    assert diff.guards_added
    assert diff.score_after > diff.score_before


def test_behavior_diff_detects_regression(spec, naive_graph, fixed_graph, scenarios):
    diff = behavior_diff(spec, fixed_graph, naive_graph, scenarios)
    assert diff.newly_failing
    assert diff.risk_after > diff.risk_before
