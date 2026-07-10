from agentproof.autofix import autofix
from agentproof.simulator import run_suite
from agentproof.team import BehaviorHistory, review


def test_commit_and_history_persist(tmp_path, spec, naive_graph, scenarios):
    history = BehaviorHistory(tmp_path)
    snap = history.commit(spec, naive_graph, scenarios, author="alice", message="initial")
    assert snap.version == 1
    assert snap.total == len(scenarios)
    # Reload from disk
    reloaded = BehaviorHistory(tmp_path)
    assert len(reloaded.snapshots) == 1
    assert reloaded.latest().author == "alice"


def test_review_approves_when_autofix_improves(tmp_path, spec, naive_graph, scenarios, fixed_graph):
    history = BehaviorHistory(tmp_path)
    history.commit(spec, naive_graph, scenarios, author="alice", message="naive")
    history.commit(spec, fixed_graph, scenarios, author="bob", message="hardened")
    request = review(history, 1, 2)
    assert request.verdict == "approve"
    assert request.diff["risk_after"] == 0
    assert "review" in request.render().lower()


def test_review_blocks_on_regression(tmp_path, spec, naive_graph, scenarios, fixed_graph):
    history = BehaviorHistory(tmp_path)
    # v1 hardened, v2 regressed back to naive
    history.commit(spec, fixed_graph, scenarios, author="alice", message="hardened")
    history.commit(spec, naive_graph, scenarios, author="bob", message="regressed")
    request = review(history, 1, 2)
    assert request.verdict == "block"
    assert request.diff["newly_failing"]
    assert any("regressed" in r.lower() or "risk" in r.lower() for r in request.reasons)


def test_review_renders_markdown(tmp_path, spec, naive_graph, scenarios, fixed_graph):
    history = BehaviorHistory(tmp_path)
    history.commit(spec, naive_graph, scenarios)
    history.commit(spec, fixed_graph, scenarios)
    text = review(history, 1, 2).render()
    assert "Behavior review" in text
    assert "Risk" in text
