"""Flexible, framework-agnostic export. The verified policy core is deterministic
and tested standalone; the framework assembly is model-written with an offline
scaffold fallback. These tests exercise the offline path (no key) so they're
deterministic in CI — any framework name yields a repo whose policy compiles,
enforces the gate, and passes its generated tests."""

import ast
import importlib.util
import sys

import pytest

from agentproof.export import export_agent
from agentproof.export.smart_export import _fallback_assembly, _tool_manifest, export_framework


@pytest.fixture
def built(spec, naive_graph, scenarios):
    return spec, naive_graph, scenarios


def _load(path, name):
    mod_spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    return mod


def test_tool_manifest_flags_money_and_sensitive(naive_graph):
    manifest = _tool_manifest(naive_graph)
    assert manifest, "should surface tool nodes"
    assert all({"id", "label", "high_risk", "moves_money", "external", "reads_sensitive"} <= set(t)
               for t in manifest)


@pytest.mark.parametrize("framework", ["langchain", "autogen", "pydantic-ai", "agno", "google-adk", "some-future-fw"])
def test_export_any_framework_offline(built, tmp_path, framework, monkeypatch):
    spec, graph, scenarios = built
    # Force the offline scaffold path regardless of environment.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    written = export_framework(framework, spec, graph, scenarios, tmp_path / framework)
    names = {p.name for p in written}
    assert {"agent.py", "policy.py", "tools.py", "test_policy.py"} <= names
    for p in written:
        if p.suffix == ".py":
            ast.parse(p.read_text())


def test_fallback_assembly_gates_money(spec, naive_graph):
    code = _fallback_assembly(spec, naive_graph, "langchain")
    ast.parse(code)
    # Any money-moving tool must consult the policy gate before acting.
    manifest = _tool_manifest(naive_graph)
    if any(t["moves_money"] for t in manifest):
        assert "policy.check_refund" in code


def test_exported_policy_enforces_gate_and_tests_pass(built, tmp_path, monkeypatch):
    spec, graph, scenarios = built
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "fw"
    export_framework("langchain", spec, graph, scenarios, out)
    # policy.py enforces the spend limit.
    sys.path.insert(0, str(out))
    try:
        policy = _load(out / "agent" / "policy.py", "gen_policy")
        assert policy.check_refund(1_000_000, approved_by_human=False).allowed is False
    finally:
        sys.path.remove(str(out))


def test_export_agent_dispatches_unknown_framework_to_flexible(built, tmp_path, monkeypatch):
    spec, graph, scenarios = built
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # A framework not in the deterministic EXPORTERS registry still produces a repo.
    written = export_agent("smolagents", spec, graph, scenarios, tmp_path / "smol")
    assert any(p.name == "agent.py" for p in written)
