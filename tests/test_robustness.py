"""Adversarial robustness: malformed inputs must fail cleanly, never crash."""

import pytest

from agentproof.importers import import_generic_json, import_python_agent
from agentproof.scenarios import generate_scenarios
from agentproof.simulator import run_suite
from agentproof.spec import parse_spec
from agentproof.studio import DEFAULT_SPEC, StudioState
from agentproof.synthesis import synthesize


@pytest.mark.parametrize("text", ["", "   \n\t ", "🤖💰", "asdf!@#$", "一个退款机器人",
                                   "handle requests " * 2000, "x\x00y"])
def test_malformed_specs_survive_full_pipeline(text):
    spec = parse_spec(text)
    graph = synthesize(spec)
    scenarios = generate_scenarios(spec, size=20)
    results = run_suite(graph, spec, scenarios)
    assert isinstance(results, list) and results


def test_python_import_rejects_syntax_error_cleanly():
    with pytest.raises(ValueError, match="parse Python"):
        import_python_agent("def foo(:\n  bad", name="x")


@pytest.mark.parametrize("bad", ["a string", [1, 2, 3], 42, None])
def test_json_import_rejects_non_object(bad):
    with pytest.raises(ValueError, match="JSON object"):
        import_generic_json(bad)


def test_json_import_handles_null_nodes():
    g = import_generic_json({"nodes": None, "edges": None})
    assert g.nodes == []


def test_studio_build_rejects_non_string_spec(tmp_path):
    st = StudioState(tmp_path)
    with pytest.raises(ValueError, match="string"):
        st.build(None)
    with pytest.raises(ValueError, match="string"):
        st.build(123)


def test_studio_build_structured_type_guards(tmp_path):
    st = StudioState(tmp_path)
    with pytest.raises(ValueError, match="guardrails"):
        st.build_structured({"guardrails": "nope"})
    with pytest.raises(ValueError, match="capabilities"):
        st.build_structured({"capabilities": "nope"})


def test_studio_add_tools_requires_list(tmp_path):
    st = StudioState(tmp_path)
    st.build(DEFAULT_SPEC)
    with pytest.raises(ValueError, match="list"):
        st.add_tools("notalist")


def test_export_framework_name_is_path_safe(tmp_path):
    st = StudioState(tmp_path)
    st.build(DEFAULT_SPEC)
    st.simulate()
    st.apply_autofix()
    res = st.export("../../../tmp/evil")
    # Sanitized to a slug that stays inside the project's export dir.
    assert ".." not in res["exported_to"]
    assert (tmp_path / "export") in list((tmp_path / "export").parents) + [tmp_path / "export"]
    for f in res["files"]:
        assert not f.startswith("..") and "/../" not in f
