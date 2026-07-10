import xml.dom.minidom
from pathlib import Path

from agentproof.cli import main

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_gate_fails_without_autofix(tmp_path, capsys):
    # Naive graph should fail the score threshold
    rc = main(["gate", str(EXAMPLES / "refund_agent.md"), "--fail-under", "85"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "GATE FAILED" in out


def test_gate_passes_with_autofix(tmp_path, capsys):
    rc = main(["gate", str(EXAMPLES / "refund_agent.md"), "--fail-under", "85", "--autofix"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "GATE PASSED" in out


def test_gate_with_pack_and_badge(tmp_path, capsys):
    badge = tmp_path / "badge.svg"
    rc = main(["gate", "--pack", "fintech", "--autofix", "--badge", str(badge)])
    assert rc == 0
    xml.dom.minidom.parseString(badge.read_text())


def test_gate_writes_github_step_summary(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    main(["gate", "--pack", "support", "--autofix"])
    text = summary.read_text()
    assert "AgentProof gate" in text
    assert "Agent Score" in text
    assert "SHIPPABLE" in text


def test_badge_command(tmp_path):
    project = tmp_path / "proj"
    main(["build", str(EXAMPLES / "refund_agent.md"), "-o", str(project)])
    main(["fix", str(project)])
    badge = tmp_path / "b.svg"
    main(["badge", str(project), "-o", str(badge)])
    xml.dom.minidom.parseString(badge.read_text())


def test_init_scaffolds_repo(tmp_path, capsys):
    rc = main(["init", "--dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "agent.spec.md").exists()
    wf = tmp_path / ".github" / "workflows" / "agentproof.yml"
    assert wf.exists()
    assert "evanl666/agentproof@main" in wf.read_text()
