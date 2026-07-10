"""PR bot — behavior review as a pull-request comment (Codecov for agents).

The single highest-leverage viral surface: when someone changes a prompt or a
tool, a bot comments on the PR showing how the agent's *behavior* moved — new
failing scenarios, risk and cost deltas, coverage drop, whether the safety proofs
still hold, and a link to the counterexample replay. Reviewers see an agent
getting more dangerous the same way they see a test breaking.

This builds the Markdown comment from a behavior diff plus the current proof and
coverage state. The GitHub Action posts it; the CLI can print it locally.
"""

from __future__ import annotations

from typing import Any

from agentproof.coverage import compute_coverage
from agentproof.coverage2 import compute_risk_coverage
from agentproof.diff import behavior_diff
from agentproof.graph import AgentGraph
from agentproof.proofs import prove
from agentproof.scenarios import Scenario
from agentproof.score import compute_score
from agentproof.simulator import run_suite
from agentproof.spec import BehaviorSpec


def build_review_comment(
    spec: BehaviorSpec,
    before: AgentGraph,
    after: AgentGraph,
    scenarios: list[Scenario],
    base_label: str = "base",
    head_label: str = "PR",
) -> str:
    diff = behavior_diff(spec, before, after, scenarios)
    results = run_suite(after, spec, scenarios)
    score = compute_score(results, compute_coverage(after, results))
    proofs = prove(after, spec)
    risk_cov = compute_risk_coverage(after, results)
    passed = sum(1 for r in results if r.passed)

    proofs_failing = [p for p in proofs if not p.holds]
    if diff.newly_failing or proofs_failing or diff.risk_after > diff.risk_before:
        verdict, emoji = "changes-requested", "🚫"
    elif diff.newly_passing or diff.score_after > diff.score_before:
        verdict, emoji = "approved", "✅"
    else:
        verdict, emoji = "no behavior change", "➖"

    def delta(a: int, b: int) -> str:
        d = b - a
        arrow = "🔺" if d > 0 else ("🔻" if d < 0 else "")
        return f"{a} → {b} {arrow}".strip()

    lines = [
        f"## {emoji} AgentProof behavior review — **{verdict}**",
        "",
        f"`{base_label}` → `{head_label}` · Agent Score {delta(diff.score_before, diff.score_after)}"
        + (f" · {'✓ SHIPPABLE' if score.shippable else '✗ NOT SHIPPABLE'}"),
        "",
        "| Signal | Base | PR |",
        "|---|---|---|",
        f"| Tests passing | — | {passed}/{len(results)} |",
        f"| Risk | {diff.risk_before} | {diff.risk_after} {'🔺' if diff.risk_after > diff.risk_before else ''} |",
        f"| Safety proofs | — | {sum(p.holds for p in proofs)}/{len(proofs)} holding |",
        f"| High-risk tool coverage | — | {round(risk_cov.high_risk_tool_coverage * 100)}% |",
        f"| Cost / suite | {diff.cost_before_tokens:,} tok | {diff.cost_after_tokens:,} tok ({diff.cost_delta_pct:+.1f}%) |",
    ]
    if diff.newly_failing:
        lines += ["", f"### 🚨 {len(diff.newly_failing)} scenario(s) newly FAIL"]
        lines += [f"- `{sid}`" for sid in diff.newly_failing[:8]]
    if proofs_failing:
        lines += ["", "### 🔓 Safety properties now VIOLATED"]
        for p in proofs_failing[:5]:
            lines.append(f"- {p.property}")
            if p.counterexample:
                lines.append(f"  - counterexample: `{' → '.join(p.counterexample)}`")
    if diff.guards_added:
        lines += ["", f"### 🛡 Guards added: {', '.join(diff.guards_added)}"]
    if diff.newly_passing:
        lines.append(f"\n✅ {len(diff.newly_passing)} scenario(s) newly pass.")
    lines += ["", "_Run `agentproof movie proj/ -o cx.html` for the counterexample replay._"]
    return "\n".join(lines)


def verdict_blocks(comment: str) -> bool:
    """True if the review comment represents a blocking (changes-requested) verdict."""
    return "changes-requested" in comment
