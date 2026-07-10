import pytest

from agentproof.spec import (
    BehaviorSpec,
    Constraint,
    ConstraintKind,
    coerce_threshold,
    parse_spec,
)
from agentproof.studio import DEFAULT_SPEC


@pytest.mark.parametrize("value,expected", [
    (100, 100.0),
    (49.5, 49.5),
    ("$100", 100.0),
    ("100 dollars", 100.0),
    ("1,250", 1250.0),
    ("order_total", 50.0),   # symbolic → safe default, must not raise
    (None, 50.0),
    ("", 50.0),
])
def test_coerce_threshold_never_crashes(value, expected):
    assert coerce_threshold(value) == expected


def test_auto_refund_limit_survives_nonnumeric_threshold():
    # The LLM parser sometimes emits a symbolic threshold; reading it must not crash.
    spec = BehaviorSpec(
        name="X",
        constraints=[Constraint(id="c1", kind=ConstraintKind.SPEND_LIMIT,
                                description="over the order total", params={"threshold": "order_total"})],
    )
    assert spec.auto_refund_limit == 50.0


def test_markdown_spec_parses_capabilities_and_constraints():
    spec = parse_spec(DEFAULT_SPEC)
    assert spec.name == "Refund support agent"
    assert len(spec.capabilities) >= 3
    kinds = {c.kind for c in spec.constraints}
    assert ConstraintKind.SPEND_LIMIT in kinds
    assert ConstraintKind.PII_EGRESS in kinds
    assert ConstraintKind.TOOL_FAILURE in kinds
    assert ConstraintKind.PROMPT_INJECTION in kinds
    assert ConstraintKind.APPROVAL_REQUIRED in kinds


def test_spend_limit_threshold_extracted():
    spec = parse_spec(DEFAULT_SPEC)
    assert spec.auto_refund_limit == 50.0


def test_prose_spec_parses():
    spec = parse_spec(
        "Build a refund support agent. Refunds under $25 are automatic. "
        "Refunds over $25 require approval. Never send PII externally."
    )
    assert spec.auto_refund_limit == 25.0
    assert spec.constraint(ConstraintKind.PII_EGRESS) is not None
    assert spec.capabilities


def test_spec_roundtrips_through_dict():
    spec = parse_spec(DEFAULT_SPEC)
    from agentproof.spec import BehaviorSpec

    clone = BehaviorSpec.from_dict(spec.to_dict())
    assert clone.name == spec.name
    assert len(clone.constraints) == len(spec.constraints)
    assert clone.auto_refund_limit == spec.auto_refund_limit
