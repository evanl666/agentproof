import pytest

from agentproof.autofix import autofix
from agentproof.packs import PACKS, get_pack, list_packs
from agentproof.simulator import run_suite
from agentproof.spec import ConstraintKind


def test_all_packs_listed():
    ids = {p.id for p in list_packs()}
    assert ids == {"support", "fintech", "healthcare"}


def test_pack_specs_parse_with_core_constraints():
    for pack in list_packs():
        spec = pack.spec()
        kinds = {c.kind for c in spec.constraints}
        assert ConstraintKind.PII_EGRESS in kinds
        assert ConstraintKind.SPEND_LIMIT in kinds
        assert ConstraintKind.PROMPT_INJECTION in kinds


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
