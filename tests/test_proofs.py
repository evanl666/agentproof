from agentproof.proofs import all_hold, proof_summary, prove


def test_naive_graph_violates_all_properties(naive_graph, spec):
    proofs = prove(naive_graph, spec)
    assert proofs
    assert not all_hold(proofs)
    kinds = {p.kind for p in proofs if not p.holds}
    assert {"spend_gated", "pii_contained", "injection_guarded"} <= kinds
    # Every violation carries a concrete counterexample path.
    for p in proofs:
        if not p.holds:
            assert p.counterexample and len(p.counterexample) >= 2


def test_fixed_graph_proves_all_properties(fixed_graph, spec):
    proofs = prove(fixed_graph, spec)
    assert proofs
    assert all_hold(proofs)
    for p in proofs:
        assert p.holds and not p.counterexample


def test_spend_counterexample_is_a_real_path(naive_graph, spec):
    proofs = prove(naive_graph, spec)
    spend = next(p for p in proofs if p.kind == "spend_gated")
    path = spend.counterexample
    # consecutive nodes must be connected by real edges
    for a, b in zip(path, path[1:]):
        assert any(e.source == a and e.target == b for e in naive_graph.edges)


def test_proof_summary_counts(naive_graph, fixed_graph, spec):
    naive = proof_summary(naive_graph, spec)
    assert naive["failing"] > 0 and not naive["all_hold"]
    fixed = proof_summary(fixed_graph, spec)
    assert fixed["failing"] == 0 and fixed["all_hold"]
