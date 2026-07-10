from agentproof.spec import ConstraintKind, parse_spec
from agentproof.studio import DEFAULT_SPEC


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
