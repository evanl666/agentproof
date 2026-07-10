import xml.dom.minidom

from agentproof.badge import render_badge, score_badge
from agentproof.coverage import compute_coverage
from agentproof.score import compute_score
from agentproof.simulator import run_suite


def _score(graph, spec, scenarios):
    results = run_suite(graph, spec, scenarios)
    return compute_score(results, compute_coverage(graph, results))


def test_badge_is_well_formed_svg(fixed_graph, spec, scenarios):
    svg = render_badge(spec, fixed_graph, scenarios)
    # Parses as XML => well-formed
    xml.dom.minidom.parseString(svg)
    assert svg.startswith("<svg")
    assert "shippable" in svg


def test_badge_reflects_score_and_verdict(naive_graph, fixed_graph, spec, scenarios):
    naive = score_badge(_score(naive_graph, spec, scenarios))
    fixed = score_badge(_score(fixed_graph, spec, scenarios))
    assert "not shippable" in naive
    assert "not shippable" not in fixed
    assert "shippable" in fixed


def test_badge_color_green_when_shippable(fixed_graph, spec, scenarios):
    svg = score_badge(_score(fixed_graph, spec, scenarios))
    assert "#3fb950" in svg  # green fill for a high, shippable score


def test_badge_label_customizable(fixed_graph, spec, scenarios):
    svg = render_badge(spec, fixed_graph, scenarios, label="agent-safety")
    assert "agent-safety" in svg
