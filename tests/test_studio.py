import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from agentproof.studio import DEFAULT_SPEC, StudioState, make_handler


def test_state_build_simulate_fix_export(tmp_path):
    state = StudioState(tmp_path)
    snapshot = state.build(DEFAULT_SPEC)
    assert snapshot["graph"]["nodes"]
    assert len(snapshot["scenarios"]) == 50

    snapshot = state.simulate()
    failed = [r for r in snapshot["results"] if not r["passed"]]
    assert failed
    assert snapshot["score"]["shippable"] is False

    snapshot = state.apply_autofix()
    assert all(r["passed"] for r in snapshot["results"])
    assert snapshot["score"]["shippable"] is True
    assert snapshot["diff"]["risk_after"] == 0
    assert snapshot["fixes"]

    result = state.export()
    assert (tmp_path / "export" / "agent" / "graph.py").exists()
    assert result["files"]


def test_state_persists_across_restarts(tmp_path):
    state = StudioState(tmp_path)
    state.build(DEFAULT_SPEC)
    reloaded = StudioState(tmp_path)
    assert reloaded.graph is not None
    assert len(reloaded.scenarios) == 50


def test_import_into_state(tmp_path, naive_graph):
    state = StudioState(tmp_path)
    snapshot = state.import_agent(
        json.dumps(naive_graph.to_dict()), "agent.json", DEFAULT_SPEC
    )
    assert snapshot["graph"]["name"] == naive_graph.name


@pytest.fixture
def studio_server(tmp_path):
    state = StudioState(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_http_endpoints(studio_server):
    def post(path, payload):
        req = urllib.request.Request(
            studio_server + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read())

    with urllib.request.urlopen(studio_server + "/") as res:
        assert b"AgentProof Studio" in res.read()

    snapshot = post("/api/build", {"spec_text": DEFAULT_SPEC})
    assert snapshot["graph"]

    snapshot = post("/api/simulate", {})
    assert snapshot["results"]

    snapshot = post("/api/autofix", {})
    assert all(r["passed"] for r in snapshot["results"])


def test_console_capabilities(tmp_path):
    """The unified console: every analysis capability works on a fixed agent."""
    state = StudioState(tmp_path)
    state.build(DEFAULT_SPEC)
    state.simulate()
    state.apply_autofix()

    proofs = state.prove()
    assert proofs["all_hold"]

    cov = state.risk_coverage()
    assert 0 <= cov["overall"] <= 1

    mut = state.mutate()
    assert mut["total"] > 0 and 0 <= mut["score"] <= 1

    cost = state.cost()
    assert cost["projection"]["per_1k_requests_usd"] > 0
    assert cost["comparison"]

    rt = state.redteam(n=6)
    assert rt["total"] > 0
    assert rt["failed"] == 0  # fixed agent holds the red-team

    audit = state.audit(turns=3)
    assert "verdict" in audit
    assert audit["breached"] == 0  # fixed agent is not breached

    comp = state.compliance()
    assert comp["score"]["shippable"]
    assert comp["controls"] and comp["proofs"]


def test_console_requires_build(tmp_path):
    state = StudioState(tmp_path)
    with pytest.raises(ValueError):
        state.prove()
