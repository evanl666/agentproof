import json
from pathlib import Path

from agentproof.cli import main

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_demo_writes_project_and_report(tmp_path, capsys):
    project = tmp_path / "demo"
    assert main(["demo", "-o", str(project)]) == 0
    out = capsys.readouterr().out
    assert "Auto-fix" in out
    assert "SHIPPABLE" in out
    assert (project / "replay.html").exists()
    assert (project / "graph.json").exists()


def test_full_pipeline_build_simulate_fix_diff_export(tmp_path, capsys):
    project = tmp_path / "proj"
    assert main(["build", str(EXAMPLES / "refund_agent.md"), "-o", str(project)]) == 0

    # First simulation fails (naive graph) -> --check exits 1.
    assert main(["simulate", str(project), "--check"]) == 1

    # Export refuses while failing.
    exported = tmp_path / "exported"
    assert main(["export", str(project), "-o", str(exported)]) == 1

    assert main(["fix", str(project)]) == 0
    assert main(["simulate", str(project), "--check"]) == 0
    assert main(["diff", str(project)]) == 0

    assert main(["export", str(project), "-o", str(exported)]) == 0
    assert (exported / "agent" / "graph.py").exists()
    out = capsys.readouterr().out
    assert "Behavior diff" in out


def test_import_command(tmp_path, capsys):
    project = tmp_path / "imported"
    assert main([
        "import", str(EXAMPLES / "existing_langgraph_agent.py"),
        "--spec", str(EXAMPLES / "refund_agent.md"),
        "-o", str(project),
    ]) == 0
    graph = json.loads((project / "graph.json").read_text())
    assert any(n["id"] == "process_refund" for n in graph["nodes"])


def test_simulate_writes_report(tmp_path):
    project = tmp_path / "proj"
    main(["build", str(EXAMPLES / "refund_agent.md"), "-o", str(project)])
    report = tmp_path / "replay.html"
    main(["simulate", str(project), "--report", str(report)])
    html = report.read_text()
    assert "Canvas replay" in html
    assert "renderGraph" in html
