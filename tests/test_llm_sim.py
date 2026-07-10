from agentproof.llm_sim import LLMDecision, LLMJudge, simulate_with_llm


def test_parse_decision_json():
    d = LLMJudge._parse('{"take_refund_action": true, "amount": 42.0, "reason": "ok"}')
    assert d.take_refund_action is True
    assert d.amount == 42.0
    assert d.reason == "ok"


def test_parse_decision_with_surrounding_text():
    d = LLMJudge._parse('Sure! {"take_refund_action": false, "amount": null, "reason": "blocked"} done')
    assert d.take_refund_action is False
    assert d.amount is None


def test_parse_unparseable_defaults_to_no_action():
    d = LLMJudge._parse("not json at all")
    assert d.take_refund_action is False
    assert d.reason == "unparseable"


class _StubJudge(LLMJudge):
    """A judge that returns a canned decision without any network call."""

    def __init__(self, decision):
        super().__init__(model="stub")
        self._decision = decision

    def decide(self, spec, scenario):
        return self._decision


def test_simulate_with_llm_declining_prevents_refund(spec, fixed_graph, scenarios):
    adversarial = next(s for s in scenarios if s.category.value == "adversarial")
    judge = _StubJudge(LLMDecision(take_refund_action=False, amount=None, reason="declined"))
    result = simulate_with_llm(fixed_graph, spec, adversarial, judge)
    assert result.passed
    assert any("stub" in n for n in result.notes)


def test_simulate_with_llm_acting_uses_amount(spec, naive_graph, scenarios):
    normal = next(s for s in scenarios if s.category.value == "normal" and s.amount)
    judge = _StubJudge(LLMDecision(take_refund_action=True, amount=999.0, reason="acting"))
    result = simulate_with_llm(naive_graph, spec, normal, judge)
    # Naive graph + over-limit amount should now trip the policy violation
    assert not result.passed
