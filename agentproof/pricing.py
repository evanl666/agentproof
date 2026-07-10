"""Cost simulator: per-model token pricing and per-path cost projection.

Production agents fail on cost as surely as they fail on safety — a graph that
loops one extra LLM call per request can double the bill at scale. AgentProof
prices every simulated path against real model rates so cost is a first-class
signal next to reliability and safety.

Prices are USD per million tokens (input, output), current as of 2026-01.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    model_id: str
    display_name: str
    input_per_mtok: float
    output_per_mtok: float
    context_window: int


# Keep IDs exact — these match the Claude API model catalog.
MODEL_PRICES: dict[str, ModelPrice] = {
    "claude-fable-5": ModelPrice("claude-fable-5", "Claude Fable 5", 10.0, 50.0, 1_000_000),
    "claude-opus-4-8": ModelPrice("claude-opus-4-8", "Claude Opus 4.8", 5.0, 25.0, 1_000_000),
    "claude-sonnet-5": ModelPrice("claude-sonnet-5", "Claude Sonnet 5", 3.0, 15.0, 1_000_000),
    "claude-haiku-4-5": ModelPrice("claude-haiku-4-5", "Claude Haiku 4.5", 1.0, 5.0, 200_000),
}

DEFAULT_MODEL = "claude-sonnet-5"

# Fraction of an LLM node's tokens that are output vs input, for blended pricing.
_OUTPUT_FRACTION = 0.25


def price_for(model_id: str) -> ModelPrice:
    return MODEL_PRICES.get(model_id, MODEL_PRICES[DEFAULT_MODEL])


def blended_rate_per_mtok(model_id: str) -> float:
    """Single $/Mtok rate assuming a fixed output/input mix."""
    p = price_for(model_id)
    return p.input_per_mtok * (1 - _OUTPUT_FRACTION) + p.output_per_mtok * _OUTPUT_FRACTION


def cost_for_tokens(tokens: int, model_id: str = DEFAULT_MODEL) -> float:
    return tokens * blended_rate_per_mtok(model_id) / 1_000_000


@dataclass
class CostReport:
    model_id: str
    total_tokens: int
    total_usd: float
    per_request_usd: float
    per_1k_requests_usd: float
    hottest_scenario: str | None
    hottest_scenario_usd: float

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "total_tokens": self.total_tokens,
            "total_usd": round(self.total_usd, 6),
            "per_request_usd": round(self.per_request_usd, 6),
            "per_1k_requests_usd": round(self.per_1k_requests_usd, 4),
            "hottest_scenario": self.hottest_scenario,
            "hottest_scenario_usd": round(self.hottest_scenario_usd, 6),
        }


def project_cost(results: list, model_id: str = DEFAULT_MODEL) -> CostReport:
    """Project the cost of a whole simulation suite at production scale.

    `results` are SimulationResult objects (duck-typed on cost_tokens/scenario).
    """
    rate = blended_rate_per_mtok(model_id)
    total_tokens = sum(r.cost_tokens for r in results)
    n = max(len(results), 1)
    total_usd = total_tokens * rate / 1_000_000
    per_request = total_usd / n
    hottest = max(results, key=lambda r: r.cost_tokens, default=None)
    return CostReport(
        model_id=model_id,
        total_tokens=total_tokens,
        total_usd=total_usd,
        per_request_usd=per_request,
        per_1k_requests_usd=per_request * 1000,
        hottest_scenario=hottest.scenario.id if hottest else None,
        hottest_scenario_usd=(hottest.cost_tokens * rate / 1_000_000) if hottest else 0.0,
    )


def compare_models(results: list) -> list[dict]:
    """Same traffic priced across every known model — the model-choice tradeoff."""
    total_tokens = sum(r.cost_tokens for r in results)
    n = max(len(results), 1)
    rows = []
    for model_id, price in MODEL_PRICES.items():
        rate = blended_rate_per_mtok(model_id)
        total = total_tokens * rate / 1_000_000
        rows.append(
            {
                "model_id": model_id,
                "display_name": price.display_name,
                "per_1k_requests_usd": round(total / n * 1000, 4),
                "input_per_mtok": price.input_per_mtok,
                "output_per_mtok": price.output_per_mtok,
            }
        )
    return sorted(rows, key=lambda r: r["per_1k_requests_usd"])
