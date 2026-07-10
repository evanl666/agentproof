"""Risk-based scenario prioritization — test the most dangerous things first.

A big agent generates hundreds of scenarios; on a tight CI budget you want the
riskiest ones first. Each scenario gets a risk weight from what it targets —
money movement and destructive actions outrank a benign question — so you can
run a `--top N` slice that concentrates on the highest blast-radius paths, or
just see the suite ordered worst-first.
"""

from __future__ import annotations

from agentproof.scenarios import Scenario, ScenarioCategory
from agentproof.spec import BehaviorSpec

_CATEGORY_WEIGHT = {
    ScenarioCategory.PROMPT_INJECTION: 90,
    ScenarioCategory.MEMORY_POISON: 90,
    ScenarioCategory.PII_LEAK: 85,
    ScenarioCategory.ADVERSARIAL: 80,
    ScenarioCategory.CONTENT_POLICY: 70,
    ScenarioCategory.BOUNDARY: 55,
    ScenarioCategory.TOOL_FAILURE: 45,
    ScenarioCategory.COST: 25,
    ScenarioCategory.NORMAL: 10,
}
_HIGH_RISK_BONUS = {"delete": 20, "admin": 18, "deploy": 15, "data_write": 8}


def risk_weight(scenario: Scenario, spec: BehaviorSpec) -> int:
    weight = _CATEGORY_WEIGHT.get(scenario.category, 20)
    if scenario.malicious:
        weight += 10
    hr = scenario.extra.get("high_risk_request")
    if hr:
        weight += _HIGH_RISK_BONUS.get(hr, 12)
    if scenario.amount is not None:
        limit = spec.auto_refund_limit or 50.0
        if scenario.amount > limit:
            # the further over the limit, the higher the blast radius
            weight += min(20, int(scenario.amount / max(limit, 1)))
    return weight


def prioritize(scenarios: list[Scenario], spec: BehaviorSpec) -> list[Scenario]:
    """Return the scenarios ordered highest-risk first (stable within a weight)."""
    return sorted(scenarios, key=lambda s: (-risk_weight(s, spec), s.id))


def top_scenarios(scenarios: list[Scenario], spec: BehaviorSpec, n: int) -> list[Scenario]:
    return prioritize(scenarios, spec)[:n]
