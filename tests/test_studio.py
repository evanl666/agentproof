import json
import threading
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

import pytest

from agentproof.studio import DEFAULT_SPEC, StudioState, Workspace, make_handler


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
    assert (tmp_path / "export" / "langgraph" / "agent" / "graph.py").exists()
    assert result["files"] and result["framework"] == "langgraph"


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
def studio_server(tmp_path, monkeypatch):
    # Deterministic build path in CI (no live model calls from Studio).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    workspace = Workspace(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(workspace))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _post(base, path, payload):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read())


def test_http_endpoints(studio_server):
    def post(path, payload):
        return _post(studio_server, path, payload)

    with urllib.request.urlopen(studio_server + "/") as res:
        assert b"AgentProof Studio" in res.read()

    snapshot = post("/api/build", {"spec_text": DEFAULT_SPEC})
    assert snapshot["graph"]

    snapshot = post("/api/simulate", {})
    assert snapshot["results"]

    snapshot = post("/api/autofix", {})
    assert all(r["passed"] for r in snapshot["results"])


def test_multi_project_dashboard_endpoints(studio_server):
    def post(path, payload):
        return _post(studio_server, path, payload)

    # Seeded workspace has one project.
    with urllib.request.urlopen(studio_server + "/api/projects") as res:
        board = json.loads(res.read())
    assert board["active"] and len(board["projects"]) == 1

    # Create a second project; it becomes active and the board lists both.
    board = post("/api/projects/new", {"name": "SQL Agent", "spec_text": DEFAULT_SPEC})
    assert len(board["projects"]) == 2
    names = {p["name"] for p in board["projects"]}
    assert "SQL Agent" in names

    # Build + simulate the active project; its score persists into the store.
    post("/api/build", {"spec_text": DEFAULT_SPEC})
    post("/api/simulate", {})
    with urllib.request.urlopen(studio_server + "/api/projects") as res:
        board = json.loads(res.read())
    active = next(p for p in board["projects"] if p["id"] == board["active"])
    assert active["total"] > 0

    # Switch back to the first project, then delete the second.
    first = next(p for p in board["projects"] if p["id"] != board["active"])
    snap = post("/api/projects/switch", {"id": first["id"]})
    assert "graph" in snap
    board2 = post("/api/projects/delete", {"id": active["id"]})
    assert active["id"] not in {p["id"] for p in board2["projects"]}


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


def test_build_structured_from_visual_editor(tmp_path):
    from agentproof.spec import ConstraintKind

    state = StudioState(tmp_path)
    snap = state.build_structured({
        "name": "Payments Ops Agent",
        "capabilities": [{"description": "Process customer refunds"},
                         {"description": "Look up customer accounts"}],
        "guardrails": {"spend_limit": True, "pii_egress": True, "prompt_injection": True},
        "spend_threshold": 200,
    })
    assert state.spec.name == "Payments Ops Agent"
    assert len(state.spec.capabilities) == 2
    kinds = {c.kind for c in state.spec.constraints}
    assert ConstraintKind.SPEND_LIMIT in kinds
    assert ConstraintKind.PII_EGRESS in kinds
    # Spend limit implies an approval escape hatch.
    assert ConstraintKind.APPROVAL_REQUIRED in kinds
    assert state.spec.auto_refund_limit == 200.0
    # The text view is kept in sync with readable, re-parseable prose.
    assert "Payments Ops Agent" in state.spec_text
    assert "$200" in state.spec_text
    assert snap["graph"]

    # And it verifies to shippable like any other build.
    state.simulate()
    state.apply_autofix()
    assert all(r.passed for r in state.results)


def test_build_structured_coerces_bad_threshold(tmp_path):
    state = StudioState(tmp_path)
    # A non-numeric threshold from the UI must not crash.
    state.build_structured({
        "name": "X",
        "capabilities": [{"description": "do a thing"}],
        "guardrails": {"spend_limit": True},
        "spend_threshold": "lots",
    })
    assert state.spec.auto_refund_limit == 50.0  # safe default


def test_tool_editing_add_update_remove(tmp_path):
    from agentproof.graph import NodeType

    state = StudioState(tmp_path)
    state.build(DEFAULT_SPEC)
    before = {n.id for n in state.graph.nodes_of_type(NodeType.TOOL)}

    # Add a high-risk tool; it must be wired into the agent loop and flagged.
    snap = state.add_tool("Delete customer account", risk={"high_risk": True})
    tid = snap["added_tool"]
    assert tid not in before
    node = state.graph.node(tid)
    assert node.type == NodeType.TOOL and node.config.get("high_risk") is True
    touching = [(e.source, e.target) for e in state.graph.edges if tid in (e.source, e.target)]
    assert len(touching) == 2  # planner -> tool -> planner

    # Editing a message invalidates stale results.
    state.simulate()
    assert state.results
    state.update_tool(tid, label="Delete account", risk={"high_risk": True, "external": True})
    assert state.results == []
    assert state.graph.node(tid).label == "Delete account"
    assert state.graph.node(tid).config.get("external") is True

    # Auto-fix must guard the new high-risk tool to shippable.
    state.simulate()
    state.apply_autofix()
    assert all(r.passed for r in state.results)

    # Remove it.
    state.remove_tool(tid)
    assert not state.graph.has_node(tid)
    assert not any(tid in (e.source, e.target) for e in state.graph.edges)


def test_tool_edit_errors(tmp_path):
    state = StudioState(tmp_path)
    with pytest.raises(ValueError):
        state.add_tool("x")  # no graph yet
    state.build(DEFAULT_SPEC)
    with pytest.raises(ValueError):
        state.add_tool("")  # empty name
    with pytest.raises(KeyError):
        state.remove_tool("does_not_exist")
    with pytest.raises(ValueError):
        state.remove_tool("planner")  # not a tool


def test_export_any_framework_and_deploy(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    state = StudioState(tmp_path)
    state.build(DEFAULT_SPEC)
    state.simulate()
    state.apply_autofix()

    # Deterministic exporter.
    lg = state.export("langgraph")
    assert lg["framework"] == "langgraph" and lg["files"]
    # Flexible (LLM-assembled, offline scaffold) exporter for an arbitrary framework.
    lc = state.export("langchain")
    assert any("agent.py" in f for f in lc["files"])

    dep = state.deploy("flyio")
    assert dep["target"] == "flyio"
    names = {Path(f).name for f in dep["files"]}
    assert {"server.py", "guards.py", "fly.toml"} <= names


def test_full_audit_shippable_vs_naive(tmp_path):
    state = StudioState(tmp_path)
    state.build(DEFAULT_SPEC)
    state.simulate()
    naive = state.full_audit()
    assert naive["verdict"] == "NOT SHIPPABLE"
    assert naive["blocking"]  # score / proofs / audit reasons
    assert naive["audit"]["breached"] > 0

    state.apply_autofix()
    fixed = state.full_audit()
    assert fixed["verdict"] == "SHIPPABLE"
    assert not fixed["blocking"]
    assert fixed["proofs"]["all_hold"]
    assert fixed["audit"]["breached"] == 0
    # the combined report carries every section
    for k in ("score", "proofs", "coverage2", "mutation", "cost", "audit", "compliance"):
        assert k in fixed


def test_studio_js_has_no_syntax_errors():
    """The whole page dies if any <script> has a syntax error (e.g. an f-string
    turning \\n into a real newline inside a JS regex). Lint every block with node
    when it's available so an all-buttons-dead regression can't slip through."""
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        import pytest as _pytest
        _pytest.skip("node not available to lint the studio JS")
    from agentproof.studio import _studio_html
    html = _studio_html()
    for i, script in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
        r = subprocess.run([node, "--check", "-"], input=script, text=True, capture_output=True)
        assert r.returncode == 0, f"script block {i} has a JS syntax error:\n{r.stderr}"
