import subprocess
import sys
import shutil

import pytest

from agentproof.export import EXPORTERS, export_agent


def test_registry_has_all_targets():
    assert set(EXPORTERS) == {"langgraph", "openai", "crewai", "typescript"}


@pytest.mark.parametrize("target", ["openai", "crewai"])
def test_python_targets_generate_compiling_code(tmp_path, spec, fixed_graph, scenarios, target):
    written = export_agent(target, spec, fixed_graph, scenarios, tmp_path)
    py_files = [p for p in written if p.suffix == ".py"]
    assert py_files
    for path in py_files:
        compile(path.read_text(), str(path), "exec")


@pytest.mark.parametrize("target", ["openai", "crewai"])
def test_python_target_policy_tests_pass(tmp_path, spec, fixed_graph, scenarios, target):
    export_agent(target, spec, fixed_graph, scenarios, tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_typescript_target_writes_policy_and_tests(tmp_path, spec, fixed_graph, scenarios):
    written = export_agent("typescript", spec, fixed_graph, scenarios, tmp_path)
    names = {str(p.relative_to(tmp_path)) for p in written}
    assert "agent/policy.ts" in names
    assert "tests/policy.test.ts" in names
    assert "package.json" in names
    policy = (tmp_path / "agent" / "policy.ts").read_text()
    assert "checkRefund" in policy
    assert "redactPii" in policy
    assert "quarantineUntrusted" in policy


def test_typescript_tests_run_on_node_if_available(tmp_path, spec, fixed_graph, scenarios):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    # Require Node >= 22 for --experimental-strip-types
    ver = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    major = int(ver.lstrip("v").split(".")[0])
    if major < 22:
        pytest.skip(f"node {ver} too old for type stripping")
    export_agent("typescript", spec, fixed_graph, scenarios, tmp_path)
    result = subprocess.run(
        [node, "--test", "--experimental-strip-types", "tests/policy.test.ts"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_export_agent_unknown_target_raises(tmp_path, spec, fixed_graph, scenarios):
    with pytest.raises(KeyError):
        export_agent("nope", spec, fixed_graph, scenarios, tmp_path)
