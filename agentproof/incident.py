"""Production incident → permanent regression test.

An agent misbehaved in production and someone filed a Sentry issue or an
incident report. This turns that one incident into a permanent guard: it
extracts the offending user input, builds a failing regression scenario,
identifies which constraint it broke, and drafts a PR body describing the guard
fix. Every incident becomes a test that can never regress silently again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentproof.replay import _classify, extract_messages
from agentproof.scenarios import Scenario
from agentproof.spec import BehaviorSpec


@dataclass
class Incident:
    title: str
    message: str
    scenario: Scenario
    suspected_kind: str

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "message": self.message,
                "scenario": self.scenario.to_dict(), "suspected_kind": self.suspected_kind}


def _incident_messages(data: Any) -> list[tuple[str, str]]:
    """Pull (title, offending user input) pairs from a Sentry/incident export."""
    records = data if isinstance(data, list) else data.get("issues") or data.get("events") or [data]
    out: list[tuple[str, str]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        title = str(rec.get("title") or rec.get("culprit") or rec.get("message") or "incident")
        # Sentry stores request/context under various keys; reuse the replay extractor.
        msgs = extract_messages(rec.get("context") or rec.get("request") or rec.get("extra") or rec)
        for m in msgs:
            out.append((title, m))
        if not msgs and isinstance(rec.get("message"), str):
            out.append((title, rec["message"]))
    return out


def incidents_to_regressions(source: str | Path | list | dict, spec: BehaviorSpec) -> list[Incident]:
    data = source if isinstance(source, (list, dict)) else json.loads(Path(source).read_text())
    out: list[Incident] = []
    for i, (title, msg) in enumerate(_incident_messages(data)):
        scen = _classify(msg, spec, i)
        scen.id = f"incident-{i:03d}"
        scen.description = f"Regression from incident: {title[:60]}"
        scen.extra = {**scen.extra, "source": "incident", "incident_title": title}
        out.append(Incident(title=title, message=msg, scenario=scen,
                            suspected_kind=scen.category.value))
    return out


def regression_pr_body(incidents: list[Incident], project: str = "proj/") -> str:
    lines = [
        "## 🛡 AgentProof: incidents → regression tests",
        "",
        f"Turned {len(incidents)} production incident(s) into permanent regression scenarios.",
        "",
        "| # | Incident | Attack type | Message |",
        "|---|---|---|---|",
    ]
    for i, inc in enumerate(incidents, 1):
        msg = inc.message.replace("|", "\\|")[:60]
        lines.append(f"| {i} | {inc.title[:40]} | `{inc.suspected_kind}` | {msg} |")
    lines += [
        "",
        "### What this PR does",
        f"- Adds the above scenarios to `{project}` so the agent is tested against these real inputs forever.",
        "- Run `agentproof fix` to auto-add the guards that stop them, then `agentproof prove --check` in CI.",
        "",
        "_Every incident becomes a test that can't regress silently again._",
    ]
    return "\n".join(lines)
