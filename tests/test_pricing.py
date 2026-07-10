from agentproof.pricing import (
    MODEL_PRICES,
    blended_rate_per_mtok,
    compare_models,
    price_for,
    project_cost,
)


def test_known_model_ids_present():
    assert "claude-sonnet-5" in MODEL_PRICES
    assert "claude-opus-4-8" in MODEL_PRICES
    assert "claude-haiku-4-5" in MODEL_PRICES
    assert "claude-fable-5" in MODEL_PRICES


def test_blended_rate_orders_by_price():
    assert blended_rate_per_mtok("claude-haiku-4-5") < blended_rate_per_mtok("claude-sonnet-5")
    assert blended_rate_per_mtok("claude-sonnet-5") < blended_rate_per_mtok("claude-opus-4-8")
    assert blended_rate_per_mtok("claude-opus-4-8") < blended_rate_per_mtok("claude-fable-5")


def test_unknown_model_falls_back():
    assert price_for("nonexistent-model").model_id == "claude-sonnet-5"


def test_project_cost(fixed_graph, spec, scenarios):
    from agentproof.simulator import run_suite

    results = run_suite(fixed_graph, spec, scenarios)
    report = project_cost(results, model_id="claude-sonnet-5")
    assert report.total_tokens > 0
    assert report.per_request_usd > 0
    assert report.per_1k_requests_usd == report.per_request_usd * 1000
    assert report.hottest_scenario is not None


def test_compare_models_sorted_cheapest_first(fixed_graph, spec, scenarios):
    from agentproof.simulator import run_suite

    results = run_suite(fixed_graph, spec, scenarios)
    rows = compare_models(results)
    costs = [r["per_1k_requests_usd"] for r in rows]
    assert costs == sorted(costs)
    assert rows[0]["model_id"] == "claude-haiku-4-5"
