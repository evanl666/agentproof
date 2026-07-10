"""Team mode: versioned behavior specs and PR-style behavior review.

Agents change over time, and a prompt tweak that looks harmless can move risk.
Team mode snapshots the spec + graph + scenario results into an append-only
history, so two revisions can be reviewed like a pull request: a behavior diff
plus a go/no-go verdict a reviewer can approve or block. The whole thing is a
JSON file under the project, so it lives in the repo next to the code.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentproof.coverage import compute_coverage
from agentproof.diff import behavior_diff
from agentproof.graph import AgentGraph
from agentproof.scenarios import Scenario
from agentproof.score import compute_score
from agentproof.simulator import run_suite
from agentproof.spec import BehaviorSpec


@dataclass
class Snapshot:
    version: int
    created_at: float
    author: str
    message: str
    spec: dict[str, Any]
    graph: dict[str, Any]
    scenarios: list[dict[str, Any]]
    score: dict[str, Any]
    passed: int
    total: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "author": self.author,
            "message": self.message,
            "spec": self.spec,
            "graph": self.graph,
            "scenarios": self.scenarios,
            "score": self.score,
            "passed": self.passed,
            "total": self.total,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Snapshot":
        return Snapshot(**data)


class BehaviorHistory:
    """Append-only snapshot history persisted to .agentproof/history.json."""

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir)
        self.snapshots: list[Snapshot] = []
        self._load()

    @property
    def _store(self) -> Path:
        return self.project_dir / ".agentproof" / "history.json"

    def _load(self) -> None:
        if self._store.exists():
            data = json.loads(self._store.read_text())
            self.snapshots = [Snapshot.from_dict(s) for s in data]

    def _save(self) -> None:
        self._store.parent.mkdir(parents=True, exist_ok=True)
        self._store.write_text(
            json.dumps([s.to_dict() for s in self.snapshots], indent=2)
        )

    def commit(
        self,
        spec: BehaviorSpec,
        graph: AgentGraph,
        scenarios: list[Scenario],
        author: str = "unknown",
        message: str = "",
    ) -> Snapshot:
        results = run_suite(graph, spec, scenarios)
        coverage = compute_coverage(graph, results)
        score = compute_score(results, coverage)
        snapshot = Snapshot(
            version=len(self.snapshots) + 1,
            created_at=time.time(),
            author=author,
            message=message,
            spec=spec.to_dict(),
            graph=graph.to_dict(),
            scenarios=[s.to_dict() for s in scenarios],
            score=score.to_dict(),
            passed=sum(1 for r in results if r.passed),
            total=len(results),
        )
        self.snapshots.append(snapshot)
        self._save()
        return snapshot

    def get(self, version: int) -> Snapshot:
        for s in self.snapshots:
            if s.version == version:
                return s
        raise KeyError(f"No snapshot version {version}")

    def latest(self) -> Snapshot | None:
        return self.snapshots[-1] if self.snapshots else None


@dataclass
class ReviewRequest:
    """A PR-style behavior review comparing two snapshot versions."""

    base_version: int
    head_version: int
    diff: dict[str, Any]
    verdict: str  # "approve" | "block" | "review"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_version": self.base_version,
            "head_version": self.head_version,
            "diff": self.diff,
            "verdict": self.verdict,
            "reasons": self.reasons,
        }

    def render(self) -> str:
        d = self.diff
        emoji = {"approve": "✅", "block": "🚫", "review": "⚠️"}[self.verdict]
        lines = [
            f"## Behavior review: v{self.base_version} → v{self.head_version}  {emoji} {self.verdict.upper()}",
            "",
            f"- Risk: {d['risk_before']} → {d['risk_after']}",
            f"- Score: {d['score_before']} → {d['score_after']}",
            f"- Cost: {d['cost_delta_pct']:+.1f}%",
            f"- Newly passing: {len(d['newly_passing'])} · Newly failing: {len(d['newly_failing'])}",
            f"- Guards added: {', '.join(d['guards_added']) or 'none'}",
            "",
            "### Verdict reasons",
        ]
        lines += [f"- {r}" for r in self.reasons]
        return "\n".join(lines)


def review(history: BehaviorHistory, base_version: int, head_version: int) -> ReviewRequest:
    base = history.get(base_version)
    head = history.get(head_version)
    spec = BehaviorSpec.from_dict(head.spec)
    scenarios = [Scenario.from_dict(s) for s in head.scenarios]
    before = AgentGraph.from_dict(base.graph)
    after = AgentGraph.from_dict(head.graph)
    diff = behavior_diff(spec, before, after, scenarios)

    reasons: list[str] = []
    verdict = "approve"
    if diff.newly_failing:
        verdict = "block"
        reasons.append(
            f"{len(diff.newly_failing)} scenario(s) newly FAIL — behavior regressed"
        )
    if diff.risk_after > diff.risk_before:
        verdict = "block"
        reasons.append(f"Risk increased {diff.risk_before} → {diff.risk_after}")
    if diff.score_after < diff.score_before:
        if verdict != "block":
            verdict = "review"
        reasons.append(f"Agent Score dropped {diff.score_before} → {diff.score_after}")
    if diff.cost_delta_pct > 25:
        if verdict == "approve":
            verdict = "review"
        reasons.append(f"Cost rose {diff.cost_delta_pct:+.1f}% — review before shipping")
    if verdict == "approve":
        gained = f" (+{len(diff.newly_passing)} scenarios fixed)" if diff.newly_passing else ""
        reasons.append(f"No regressions; risk {diff.risk_before}→{diff.risk_after}{gained}")

    return ReviewRequest(
        base_version=base_version,
        head_version=head_version,
        diff=diff.to_dict(),
        verdict=verdict,
        reasons=reasons,
    )
