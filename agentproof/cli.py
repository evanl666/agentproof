"""AgentProof CLI: the agent CI pipeline as commands.

    agentproof demo                     # the full story in one command
    agentproof build spec.md -o proj/   # spec -> graph + scenarios
    agentproof simulate proj/           # run the arena
    agentproof fix proj/                # auto-repair + re-verify
    agentproof diff proj/               # behavior diff vs baseline
    agentproof report proj/ -o out.html # canvas replay report
    agentproof export proj/ -o agent/   # production LangGraph repo
    agentproof import existing.py -o proj/  # lift an existing agent
    agentproof studio                   # local visual IDE
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentproof import __version__
from agentproof.autofix import autofix
from agentproof.coverage import compute_coverage
from agentproof.diff import behavior_diff
from agentproof.export import export_langgraph
from agentproof.graph import AgentGraph
from agentproof.importers import import_agent
from agentproof.report import write_report
from agentproof.scenarios import Scenario, generate_scenarios
from agentproof.score import compute_score
from agentproof.simulator import run_suite
from agentproof.spec import BehaviorSpec, parse_spec
from agentproof.studio import DEFAULT_SPEC, serve
from agentproof.synthesis import synthesize

GREEN, RED, YELLOW, CYAN, BOLD, DIM, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[1m", "\033[2m", "\033[0m",
)


def _c(color: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{color}{text}{RESET}"


# -- project persistence ---------------------------------------------------

def _save(project: Path, **artifacts) -> None:
    project.mkdir(parents=True, exist_ok=True)
    for name, value in artifacts.items():
        (project / f"{name}.json").write_text(json.dumps(value, indent=2))


def _load_spec(project: Path) -> BehaviorSpec:
    return BehaviorSpec.from_dict(json.loads((project / "spec.json").read_text()))


def _load_graph(project: Path, name: str = "graph") -> AgentGraph:
    return AgentGraph.from_dict(json.loads((project / f"{name}.json").read_text()))


def _load_scenarios(project: Path) -> list[Scenario]:
    return [
        Scenario.from_dict(s)
        for s in json.loads((project / "scenarios.json").read_text())
    ]


# -- commands ---------------------------------------------------------------

def cmd_build(args: argparse.Namespace) -> int:
    spec_text = Path(args.spec).read_text() if args.spec else DEFAULT_SPEC
    spec = parse_spec(spec_text)
    graph = synthesize(spec)
    scenarios = generate_scenarios(spec, seed=args.seed, size=args.size)
    project = Path(args.out)
    _save(
        project,
        spec=spec.to_dict(),
        graph=graph.to_dict(),
        baseline_graph=graph.to_dict(),
        scenarios=[s.to_dict() for s in scenarios],
    )
    (project / "spec.md").write_text(spec_text)
    print(f"{_c(BOLD, spec.name)}")
    print(f"  capabilities: {len(spec.capabilities)}   constraints: {len(spec.constraints)}")
    print(f"  graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges")
    print(f"  scenarios: {len(scenarios)} generated (seeded, deterministic)")
    print(f"  project written to {_c(CYAN, str(project))}")
    print(f"\nNext: {_c(BOLD, f'agentproof simulate {project}')}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    graph = import_agent(args.file)
    spec_text = Path(args.spec).read_text() if args.spec else DEFAULT_SPEC
    spec = parse_spec(spec_text)
    scenarios = generate_scenarios(spec, seed=args.seed, size=args.size)
    project = Path(args.out)
    _save(
        project,
        spec=spec.to_dict(),
        graph=graph.to_dict(),
        baseline_graph=graph.to_dict(),
        scenarios=[s.to_dict() for s in scenarios],
    )
    (project / "spec.md").write_text(spec_text)
    print(f"Imported {_c(BOLD, args.file)} -> {len(graph.nodes)} nodes, {len(graph.edges)} edges")
    if not args.spec:
        print(_c(YELLOW, "  (no --spec given: using the default refund-agent contract; edit spec.md)"))
    print(f"\nNext: {_c(BOLD, f'agentproof simulate {project}')}")
    return 0


def _print_results(results, coverage, score) -> None:
    by_category: dict[str, list] = {}
    for r in results:
        by_category.setdefault(r.scenario.category.value, []).append(r)
    for category, rs in by_category.items():
        passed = sum(1 for r in rs if r.passed)
        status = _c(GREEN, f"{passed}/{len(rs)}") if passed == len(rs) else _c(RED, f"{passed}/{len(rs)}")
        print(f"  {category:<18} {status}")
        for r in rs:
            if not r.passed:
                for v in r.violations:
                    print(f"    {_c(RED, '✗')} {r.scenario.id}: {v.message}")
    total_passed = sum(1 for r in results if r.passed)
    total_cost = sum(r.cost_usd for r in results)
    verdict = (
        _c(GREEN, f"PASSED {total_passed}/{len(results)}")
        if total_passed == len(results)
        else _c(RED, f"FAILED {len(results) - total_passed}/{len(results)}")
    )
    print(f"\n  {verdict}   coverage {round(coverage.overall * 100)}%   "
          f"cost ${total_cost:.2f}/{len(results)} req")
    print(
        f"  score: reliability {score.reliability} · safety {score.safety} · "
        f"cost {score.cost_efficiency} · coverage {score.coverage} · autonomy {score.autonomy}"
    )
    ship = _c(GREEN, f"✓ SHIPPABLE ({score.overall}/100)") if score.shippable else _c(
        RED, f"✗ NOT SHIPPABLE ({score.overall}/100)"
    )
    print(f"  {ship}")


def cmd_simulate(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph, scenarios = _load_spec(project), _load_graph(project), _load_scenarios(project)
    print(f"{_c(BOLD, 'Simulation arena')} · {len(scenarios)} scenarios vs {spec.name}\n")
    results = run_suite(graph, spec, scenarios)
    coverage = compute_coverage(graph, results)
    score = compute_score(results, coverage)
    _save(project, results=[r.to_dict() for r in results])
    _print_results(results, coverage, score)
    if args.report:
        path = write_report(args.report, spec, graph, results, coverage, score)
        print(f"\n  canvas replay: {_c(CYAN, str(path))}")
    failed = sum(1 for r in results if not r.passed)
    if failed:
        print(f"\nNext: {_c(BOLD, f'agentproof fix {project}')}")
    return 1 if failed and args.check else 0


def cmd_fix(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph, scenarios = _load_spec(project), _load_graph(project), _load_scenarios(project)
    results = run_suite(graph, spec, scenarios)
    report = autofix(graph, spec, results)
    if not report.fixes:
        print("Nothing to fix: all scenarios already pass.")
        return 0
    print(_c(BOLD, "Auto-fix applied:"))
    for fix in report.fixes:
        print(f"  {_c(GREEN, '+')} {fix.description}")
        print(f"      nodes added: {', '.join(fix.nodes_added)}")
    new_results = run_suite(report.graph, spec, scenarios)
    coverage = compute_coverage(report.graph, new_results)
    score = compute_score(new_results, coverage)
    _save(
        project,
        graph=report.graph.to_dict(),
        results=[r.to_dict() for r in new_results],
    )
    print(f"\n{_c(BOLD, 'Re-verification:')}")
    _print_results(new_results, coverage, score)
    print(f"\nNext: {_c(BOLD, f'agentproof diff {project}')} · "
          f"{_c(BOLD, f'agentproof export {project} -o ./my-agent')}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec = _load_spec(project)
    before = _load_graph(project, "baseline_graph")
    after = _load_graph(project)
    scenarios = _load_scenarios(project)
    diff = behavior_diff(spec, before, after, scenarios)
    print(_c(BOLD, "Behavior diff (baseline -> current)"))
    print(f"  risk        {diff.risk_before} -> {diff.risk_after}")
    print(f"  score       {diff.score_before} -> {diff.score_after}")
    print(f"  cost        {diff.cost_before_tokens:,} -> {diff.cost_after_tokens:,} tokens "
          f"({diff.cost_delta_pct:+.1f}%)")
    print(f"  newly passing  {len(diff.newly_passing)}")
    print(f"  newly failing  {len(diff.newly_failing)}")
    if diff.newly_failing:
        for sid in diff.newly_failing:
            print(f"    {_c(RED, '✗')} {sid}")
    print(f"  guards added   {', '.join(diff.guards_added) or 'none'}")
    print(f"  tools added    {', '.join(diff.tools_added) or 'none'}")
    return 1 if diff.newly_failing and args.check else 0


def cmd_report(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph, scenarios = _load_spec(project), _load_graph(project), _load_scenarios(project)
    results = run_suite(graph, spec, scenarios)
    coverage = compute_coverage(graph, results)
    score = compute_score(results, coverage)
    path = write_report(args.out, spec, graph, results, coverage, score)
    print(f"Canvas replay written to {_c(CYAN, str(path))}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph, scenarios = _load_spec(project), _load_graph(project), _load_scenarios(project)
    results = run_suite(graph, spec, scenarios)
    failed = sum(1 for r in results if not r.passed)
    if failed and not args.force:
        print(_c(RED, f"Refusing to export: {failed} scenarios still failing."))
        print(f"Run {_c(BOLD, f'agentproof fix {project}')} first, or pass --force.")
        return 1
    written = export_langgraph(spec, graph, scenarios, args.out)
    print(f"{_c(BOLD, 'Exported production repo:')} {args.out}")
    for path in written:
        print(f"  {path}")
    return 0


def cmd_studio(args: argparse.Namespace) -> int:
    serve(args.dir, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    print(_c(BOLD, "AgentProof demo: refund support agent\n"))
    print(_c(DIM, DEFAULT_SPEC))
    spec = parse_spec(DEFAULT_SPEC)
    graph = synthesize(spec)
    scenarios = generate_scenarios(spec)
    print(f"Synthesized graph: {len(graph.nodes)} nodes, {len(graph.edges)} edges")
    print(f"Generated {len(scenarios)} simulation scenarios\n")

    print(_c(BOLD, "── First run (naive graph) ─────────────────────────"))
    results = run_suite(graph, spec, scenarios)
    coverage = compute_coverage(graph, results)
    _print_results(results, coverage, compute_score(results, coverage))

    report = autofix(graph, spec, results)
    print(f"\n{_c(BOLD, '── Auto-fix ────────────────────────────────────────')}")
    for fix in report.fixes:
        print(f"  {_c(GREEN, '+')} {fix.description}")

    print(f"\n{_c(BOLD, '── Second run (repaired graph) ─────────────────────')}")
    new_results = run_suite(report.graph, spec, scenarios)
    new_coverage = compute_coverage(report.graph, new_results)
    _print_results(new_results, new_coverage, compute_score(new_results, new_coverage))

    diff = behavior_diff(spec, graph, report.graph, scenarios)
    print(f"\n{_c(BOLD, '── Behavior diff ───────────────────────────────────')}")
    print(f"  risk {diff.risk_before} -> {diff.risk_after} · "
          f"score {diff.score_before} -> {diff.score_after} · "
          f"guards added: {', '.join(diff.guards_added)}")

    if args.out:
        project = Path(args.out)
        _save(
            project,
            spec=spec.to_dict(),
            graph=report.graph.to_dict(),
            baseline_graph=graph.to_dict(),
            scenarios=[s.to_dict() for s in scenarios],
            results=[r.to_dict() for r in new_results],
        )
        (project / "spec.md").write_text(DEFAULT_SPEC)
        path = write_report(
            project / "replay.html", spec, report.graph, new_results, new_coverage,
            compute_score(new_results, new_coverage), fixes=report.fixes,
        )
        print(f"\n  project: {_c(CYAN, str(project))}")
        print(f"  canvas replay: {_c(CYAN, str(path))}")
    print(f"\nTry the visual IDE: {_c(BOLD, 'agentproof studio')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentproof",
        description="Don't just build agents. Prove they behave.",
    )
    parser.add_argument("--version", action="version", version=f"agentproof {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("demo", help="run the full pipeline on the refund-agent example")
    p.add_argument("-o", "--out", help="also write the project + replay report here")
    p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("build", help="compile a behavior spec into a graph + scenarios")
    p.add_argument("spec", nargs="?", help="spec file (markdown or prose); default example")
    p.add_argument("-o", "--out", default="./agentproof-project")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--size", type=int, default=50)
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("import", help="import an existing agent (LangGraph .py, Flowise/JSON)")
    p.add_argument("file")
    p.add_argument("--spec", help="behavior spec to verify the imported agent against")
    p.add_argument("-o", "--out", default="./agentproof-project")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--size", type=int, default=50)
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("simulate", help="run the simulation arena")
    p.add_argument("project")
    p.add_argument("--report", help="write a canvas replay HTML report")
    p.add_argument("--check", action="store_true", help="exit 1 on failures (for CI)")
    p.set_defaults(fn=cmd_simulate)

    p = sub.add_parser("fix", help="auto-repair the graph and re-verify")
    p.add_argument("project")
    p.set_defaults(fn=cmd_fix)

    p = sub.add_parser("diff", help="behavior diff: baseline vs current graph")
    p.add_argument("project")
    p.add_argument("--check", action="store_true", help="exit 1 if anything newly fails")
    p.set_defaults(fn=cmd_diff)

    p = sub.add_parser("report", help="write the canvas replay HTML report")
    p.add_argument("project")
    p.add_argument("-o", "--out", default="replay.html")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("export", help="export a production LangGraph repo")
    p.add_argument("project")
    p.add_argument("-o", "--out", default="./exported-agent")
    p.add_argument("--force", action="store_true", help="export even with failing scenarios")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("studio", help="launch the local visual IDE")
    p.add_argument("--dir", default=".")
    p.add_argument("--port", type=int, default=4517)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_studio)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
