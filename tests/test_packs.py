import pytest

from agentproof.autofix import autofix
from agentproof.packs import PACKS, get_pack, list_packs
from agentproof.simulator import run_suite
from agentproof.spec import ConstraintKind


def test_all_packs_listed():
    ids = {p.id for p in list_packs()}
    assert {"support", "fintech", "healthcare", "coding", "sql", "sales"} <= ids


def test_pack_specs_parse_with_core_constraints():
    # Every pack declares prompt-injection defense and at least one
    # egress/high-risk guardrail — but which one is domain-specific.
    for pack in list_packs():
        spec = pack.spec()
        kinds = {c.kind for c in spec.constraints}
        assert ConstraintKind.PROMPT_INJECTION in kinds
        assert kinds & {
            ConstraintKind.PII_EGRESS,
            ConstraintKind.SENSITIVE_EGRESS,
            ConstraintKind.SPEND_LIMIT,
            ConstraintKind.HIGH_RISK_ACTION,
        }


@pytest.mark.parametrize("pack_id", ["coding", "sql", "sales"])
def test_non_refund_packs_are_generic(pack_id):
    from agentproof.coverage import compute_coverage
    from agentproof.proofs import all_hold, prove
    from agentproof.score import compute_score
    from agentproof.synthesis import synthesize

    pack = get_pack(pack_id)
    spec = pack.spec()
    scenarios = pack.scenarios()
    graph = synthesize(spec)
    before = run_suite(graph, spec, scenarios)
    assert any(not r.passed for r in before), f"{pack_id} should have violations"
    repaired = autofix(graph, spec, before).graph
    after = run_suite(repaired, spec, scenarios)
    assert all(r.passed for r in after)
    assert compute_score(after, compute_coverage(repaired, after)).shippable
    assert all_hold(prove(repaired, spec))


def test_pack_scenarios_include_extras():
    pack = get_pack("fintech")
    scenarios = pack.scenarios()
    ids = {s.id for s in scenarios}
    assert any(sid.startswith("fintech-pack") for sid in ids)
    assert len(scenarios) > 50  # base suite + pack extras


@pytest.mark.parametrize("pack_id", ["support", "fintech", "healthcare"])
def test_each_pack_fails_naive_then_autofix_repairs(pack_id):
    from agentproof.synthesis import synthesize

    pack = get_pack(pack_id)
    spec = pack.spec()
    graph = synthesize(spec)
    scenarios = pack.scenarios()
    results = run_suite(graph, spec, scenarios)
    assert any(not r.passed for r in results), f"{pack_id} naive graph should fail"
    repaired = autofix(graph, spec, results).graph
    after = run_suite(repaired, spec, scenarios)
    failing = [r for r in after if not r.passed]
    assert not failing, [(r.scenario.id, [v.message for v in r.violations]) for r in failing]


def test_unknown_pack_raises():
    with pytest.raises(KeyError):
        get_pack("nope")
