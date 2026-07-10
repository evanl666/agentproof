from agentproof.policy_lines import compute_policy_lines, policy_summary


def test_naive_graph_has_open_policy_lines(naive_graph, spec):
    lines = compute_policy_lines(naive_graph, spec)
    assert lines
    open_lines = [line for line in lines if not line.satisfied]
    assert open_lines
    kinds = {line.kind for line in open_lines}
    assert "pii_egress" in kinds
    assert "spend_limit" in kinds
    assert "prompt_injection" in kinds


def test_fixed_graph_satisfies_all_policy_lines(fixed_graph, spec):
    lines = compute_policy_lines(fixed_graph, spec)
    assert lines
    assert all(line.satisfied for line in lines), [
        line.to_dict() for line in lines if not line.satisfied
    ]


def test_policy_summary_counts(naive_graph, fixed_graph, spec):
    naive = policy_summary(naive_graph, spec)
    assert naive["open"] > 0
    assert naive["satisfied"] + naive["open"] == naive["total"]
    fixed = policy_summary(fixed_graph, spec)
    assert fixed["open"] == 0
