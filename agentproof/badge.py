"""Agent Score badge: a self-contained SVG for READMEs and dashboards.

A badge in a repo's README is the cheapest, most viral proof that an agent was
verified. `agentproof badge proj/ -o badge.svg` renders the current Agent Score
(and its shippable verdict) as a shields.io-style SVG with no external calls —
drop it next to your CI badge.
"""

from __future__ import annotations

from pathlib import Path

from agentproof.coverage import compute_coverage
from agentproof.graph import AgentGraph
from agentproof.scenarios import Scenario
from agentproof.score import AgentScore, compute_score
from agentproof.simulator import run_suite
from agentproof.spec import BehaviorSpec

# Score band -> hex color, matching the shields.io conventions.
_COLORS = [
    (90, "#3fb950"),  # green
    (75, "#94b91e"),  # yellow-green
    (60, "#d29922"),  # amber
    (0, "#f85149"),   # red
]

# Approximate character width in the 11px Verdana shields use, ×10.
_CHAR_W = 6.5


def _color(score: int) -> str:
    for threshold, hex_color in _COLORS:
        if score >= threshold:
            return hex_color
    return "#f85149"


def _text_width(text: str) -> float:
    return len(text) * _CHAR_W + 10


def _badge_svg(label: str, message: str, color: str) -> str:
    lw = _text_width(label)
    mw = _text_width(message)
    total = lw + mw
    label_x = lw / 2 * 10
    msg_x = (lw + mw / 2) * 10
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total:.0f}" height="20" role="img" aria-label="{label}: {message}">
  <title>{label}: {message}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{total:.0f}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{lw:.0f}" height="20" fill="#555"/>
    <rect x="{lw:.0f}" width="{mw:.0f}" height="20" fill="{color}"/>
    <rect width="{total:.0f}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="110" text-rendering="geometricPrecision">
    <text x="{label_x:.0f}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(lw - 10) * 10:.0f}">{label}</text>
    <text x="{label_x:.0f}" y="140" transform="scale(.1)" textLength="{(lw - 10) * 10:.0f}">{label}</text>
    <text x="{msg_x:.0f}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(mw - 10) * 10:.0f}">{message}</text>
    <text x="{msg_x:.0f}" y="140" transform="scale(.1)" textLength="{(mw - 10) * 10:.0f}">{message}</text>
  </g>
</svg>"""


def score_badge(score: AgentScore, label: str = "agentproof") -> str:
    verdict = "shippable" if score.shippable else "not shippable"
    message = f"{score.overall}/100 · {verdict}"
    color = _color(score.overall) if score.shippable else "#f85149"
    return _badge_svg(label, message, color)


def render_badge(
    spec: BehaviorSpec, graph: AgentGraph, scenarios: list[Scenario], label: str = "agentproof"
) -> str:
    results = run_suite(graph, spec, scenarios)
    coverage = compute_coverage(graph, results)
    score = compute_score(results, coverage)
    return score_badge(score, label=label)


def write_badge(
    path: str | Path,
    spec: BehaviorSpec,
    graph: AgentGraph,
    scenarios: list[Scenario],
    label: str = "agentproof",
) -> Path:
    path = Path(path)
    path.write_text(render_badge(spec, graph, scenarios, label=label))
    return path
