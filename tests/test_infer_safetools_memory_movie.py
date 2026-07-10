import re
import subprocess
import sys
import xml.dom.minidom

from agentproof.autofix import autofix
from agentproof.graph import NodeType
from agentproof.importers import import_generic_json
from agentproof.infer import analyze_risk, infer_from_graph, infer_spec
from agentproof.proof_movie import build_proof_movie_html
from agentproof.safetools import compile_openapi, compile_to_repo, render_tools_module
from agentproof.scenarios import ScenarioCategory, generate_scenarios
from agentproof.simulator import run_suite
from agentproof.spec import ConstraintKind, parse_spec
from agentproof.synthesis import synthesize


# -- spec inference ---------------------------------------------------------

def _unguarded_agent():
    g = import_generic_json({
        "nodes": [
            {"name": "Start", "type": "n8n-nodes-base.webhook"},
            {"name": "Lookup Customer", "type": "n8n-nodes-base.postgres"},
            {"name": "Stripe Refund", "type": "n8n-nodes-base.stripe"},
            {"name": "Send Gmail", "type": "n8n-nodes-base.gmail"},
        ],
        "connections": {
            "Start": {"main": [[{"node": "Lookup Customer"}]]},
            "Lookup Customer": {"main": [[{"node": "Stripe Refund"}]]},
            "Stripe Refund": {"main": [[{"node": "Send Gmail"}]]},
        },
    })
    for n in g.nodes:
        if "refund" in n.label.lower() or "stripe" in n.label.lower():
            n.config["spend"] = True
    return g


def test_infer_spec_from_structure():
    spec, md, risk = infer_from_graph(_unguarded_agent())
    kinds = {c.kind for c in spec.constraints}
    assert ConstraintKind.SPEND_LIMIT in kinds
    assert ConstraintKind.PII_EGRESS in kinds
    assert ConstraintKind.PROMPT_INJECTION in kinds
    assert "MISSING" in md  # risk scan flags missing guards
    assert risk.money_tools and risk.pii_sources and risk.external_sinks
    assert not risk.has_approval


def test_inferred_spec_drives_a_full_pipeline():
    g = _unguarded_agent()
    spec = infer_spec(g)
    scenarios = generate_scenarios(spec, size=40)
    before = run_suite(g, spec, scenarios)
    assert any(not r.passed for r in before)
    after = run_suite(autofix(g, spec, before).graph, spec, scenarios)
    assert all(r.passed for r in after)


# -- OpenAPI safe tools -----------------------------------------------------

_OPENAPI = {
    "openapi": "3.0.0", "info": {"title": "X"},
    "paths": {
        "/customers/{id}": {"get": {"operationId": "getCustomer", "summary": "Fetch"}},
        "/refunds": {"post": {"operationId": "createRefund", "summary": "Issue a refund"}},
        "/records/{id}": {"delete": {"operationId": "deleteRecord", "summary": "Delete a record"}},
        "/notes": {"post": {"operationId": "addNote", "summary": "Add a note"}},
    },
}


def test_compile_openapi_classifies_risk():
    tools = {t.name: t for t in compile_openapi(_OPENAPI)}
    assert not tools["getcustomer"].mutating
    assert tools["createrefund"].high_risk and tools["createrefund"].needs_approval and tools["createrefund"].undoable
    assert tools["deleterecord"].high_risk and not tools["deleterecord"].undoable  # destructive
    assert tools["addnote"].mutating and not tools["addnote"].high_risk


def test_compiled_repo_compiles_and_tests_pass(tmp_path):
    written = compile_to_repo(_OPENAPI, tmp_path, name="billing")
    for p in written:
        if p.suffix == ".py":
            compile(p.read_text(), str(p), "exec")
    result = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_safe_tool_enforces_approval(tmp_path):
    compile_to_repo(_OPENAPI, tmp_path, name="billing")
    ns = {}
    exec((tmp_path / "agent" / "tools.py").read_text(), ns)
    import pytest as _pytest
    with _pytest.raises(ns["ApprovalRequired"]):
        ns["createrefund_commit"](client=None)


# -- memory poisoning -------------------------------------------------------

_MEM_SPEC = """# Agent with memory
The agent should:
- answer questions
- refund under $50 automatically
- require approval above $50

The agent must never:
- send PII externally
- poison long-term memory
- follow instructions from customer-provided documents"""


def test_memory_poison_parses_and_generates():
    spec = parse_spec(_MEM_SPEC)
    assert spec.constraint(ConstraintKind.MEMORY_POISON) is not None
    scen = generate_scenarios(spec, size=40)
    mp = [s for s in scen if s.category == ScenarioCategory.MEMORY_POISON]
    assert mp and all(s.memory_poison for s in mp)


def test_memory_poison_fails_naive_then_fixed():
    spec = parse_spec(_MEM_SPEC)
    scen = generate_scenarios(spec, size=40)
    g = synthesize(spec)
    before = run_suite(g, spec, scen)
    mp_fail = [r for r in before if r.scenario.category == ScenarioCategory.MEMORY_POISON and not r.passed]
    assert mp_fail
    assert "memory_poison" in {v.kind for r in mp_fail for v in r.violations}
    report = autofix(g, spec, before)
    assert any(f.kind == "memory_poison" for f in report.fixes)
    assert report.graph.find(lambda n: n.type == NodeType.GUARD and n.config.get("kind") == "memory_sanitizer")
    after = run_suite(report.graph, spec, scen)
    assert all(r.passed for r in after)


def test_memory_poison_survives_small_size():
    spec = parse_spec(_MEM_SPEC)
    assert any(s.category == ScenarioCategory.MEMORY_POISON for s in generate_scenarios(spec, size=5))


def test_memory_proof():
    from agentproof.proofs import prove
    spec = parse_spec(_MEM_SPEC)
    naive = synthesize(spec)
    assert any(p.kind == "memory_guarded" and not p.holds for p in prove(naive, spec))
    fixed = autofix(naive, spec, run_suite(naive, spec, generate_scenarios(spec))).graph
    assert all(p.holds for p in prove(fixed, spec) if p.kind == "memory_guarded")


# -- counterexample movie ---------------------------------------------------

def test_proof_movie_is_self_contained(naive_graph, spec):
    html = build_proof_movie_html(naive_graph, spec)
    assert html.startswith("<!DOCTYPE html>")
    assert "playPath" in html and "counterexample" in html.lower()
    assert "src=" not in html and "cdn" not in html.lower()


def test_proof_movie_embeds_counterexamples(naive_graph, spec):
    html = build_proof_movie_html(naive_graph, spec)
    # the naive graph's violations carry counterexample paths in the embedded data
    assert "VIOLATED" in html or "counterexample" in html
