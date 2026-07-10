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

import os

from agentproof import __version__
from agentproof.autofix import autofix
from agentproof.badge import score_badge, write_badge
from agentproof.coverage import compute_coverage
from agentproof.diff import behavior_diff
from agentproof.export import EXPORTERS, export_agent, export_langgraph
from agentproof.graph import AgentGraph
from agentproof.importers import detect_format, import_agent
from agentproof.packs import get_pack, list_packs
from agentproof.policy_lines import compute_policy_lines, policy_summary
from agentproof.pricing import MODEL_PRICES, compare_models, project_cost
from agentproof.report import write_report
from agentproof.scenarios import Scenario, generate_scenarios
from agentproof.score import compute_score
from agentproof.simulator import run_suite
from agentproof.spec import BehaviorSpec, parse_spec
from agentproof.agentworld import AgentWorld
from agentproof.infer import infer_from_graph
from agentproof.middleware import export_middleware
from agentproof.playground import write_playground
from agentproof.probe import http_agent, probe_agent, probe_summary
from agentproof.proof_movie import write_proof_movie
from agentproof.proofs import prove, proof_summary
from agentproof.safetools import compile_to_repo
from agentproof.redteam import ClaudeRedTeam, redteam_scenarios
from agentproof.replay import traces_to_scenarios
from agentproof.studio import DEFAULT_SPEC, serve
from agentproof.synthesis import synthesize
from agentproof.team import BehaviorHistory, review

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
    if getattr(args, "pack", None):
        pack = get_pack(args.pack)
        spec_text = pack.spec_text
        spec = pack.spec()
        graph = synthesize(spec)  # packs stay deterministic for stability
        scenarios = pack.scenarios(seed=args.seed, size=args.size)
        print(f"{_c(BOLD, f'Using {pack.name} pack')}: {pack.description}")
    else:
        from agentproof.intelligence import smart_generate_scenarios, smart_synthesize, use_llm
        from agentproof.smart import smart_parse_spec

        spec_text = Path(args.spec).read_text() if args.spec else DEFAULT_SPEC
        smart = use_llm() and not getattr(args, "no_llm", False)
        if smart:
            spec = smart_parse_spec(spec_text)
            graph = smart_synthesize(spec)
            print(_c(DIM, "🧠 LLM-native: spec parsed and tools designed by the model"))
        else:
            spec = parse_spec(spec_text)
            graph = synthesize(spec)
        if smart and getattr(args, "smart_scenarios", False):
            scenarios = smart_generate_scenarios(spec, n=args.size, seed=args.seed)
        else:
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
    written = export_agent(args.target, spec, graph, scenarios, args.out)
    print(f"{_c(BOLD, f'Exported {args.target} repo:')} {args.out}")
    for path in written:
        print(f"  {path}")
    return 0


def cmd_packs(args: argparse.Namespace) -> int:
    print(_c(BOLD, "Domain scenario packs:"))
    for pack in list_packs():
        print(f"  {_c(CYAN, pack.id):<20} {pack.name} — {pack.description}")
    print(f"\nUse: {_c(BOLD, 'agentproof build --pack fintech -o proj/')}")
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph, scenarios = _load_spec(project), _load_graph(project), _load_scenarios(project)
    results = run_suite(graph, spec, scenarios)
    report = project_cost(results, model_id=args.model)
    print(f"{_c(BOLD, 'Cost projection')} · model {report.model_id}")
    print(f"  total simulated tokens : {report.total_tokens:,}")
    print(f"  per request            : ${report.per_request_usd:.5f}")
    print(f"  per 1,000 requests     : ${report.per_1k_requests_usd:.2f}")
    print(f"  hottest scenario       : {report.hottest_scenario} "
          f"(${report.hottest_scenario_usd:.5f})")
    print(f"\n{_c(BOLD, 'Model comparison (per 1,000 requests):')}")
    for row in compare_models(results):
        print(f"  {row['display_name']:<20} ${row['per_1k_requests_usd']:.2f}")
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph = _load_spec(project), _load_graph(project)
    lines = compute_policy_lines(graph, spec)
    print(_c(BOLD, "Policy lines (contract drawn on the graph):"))
    open_count = 0
    for line in lines:
        mark = _c(GREEN, "✓") if line.satisfied else _c(RED, "✗ OPEN")
        if not line.satisfied:
            open_count += 1
        print(f"  {mark}  {line.source} ⇒ {line.target}: {line.label}")
        print(f"        {_c(DIM, line.detail)}")
    verdict = (
        _c(GREEN, "all policy lines satisfied")
        if open_count == 0
        else _c(RED, f"{open_count} policy line(s) OPEN")
    )
    print(f"\n  {verdict}")
    return 1 if open_count and args.check else 0


def cmd_commit(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph, scenarios = _load_spec(project), _load_graph(project), _load_scenarios(project)
    history = BehaviorHistory(project)
    snapshot = history.commit(spec, graph, scenarios, author=args.author, message=args.message)
    print(f"{_c(GREEN, 'Committed')} behavior snapshot "
          f"{_c(BOLD, f'v{snapshot.version}')} by {snapshot.author}")
    print(f"  {snapshot.passed}/{snapshot.total} passing · score {snapshot.score['overall']}")
    if snapshot.version > 1:
        print(f"\nReview: {_c(BOLD, f'agentproof review {project} {snapshot.version - 1} {snapshot.version}')}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    project = Path(args.project)
    history = BehaviorHistory(project)
    if not history.snapshots:
        print(_c(RED, "No snapshots yet. Run `agentproof commit` first."))
        return 1
    base = args.base if args.base is not None else max(1, len(history.snapshots) - 1)
    head = args.head if args.head is not None else len(history.snapshots)
    request = review(history, base, head)
    print(request.render())
    return 1 if request.verdict == "block" and args.check else 0


def cmd_badge(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph, scenarios = _load_spec(project), _load_graph(project), _load_scenarios(project)
    path = write_badge(args.out, spec, graph, scenarios, label=args.label)
    print(f"Agent Score badge written to {_c(CYAN, str(path))}")
    return 0


def _resolve_spec_and_scenarios(args):
    """Shared spec/scenario loading for gate: --pack, spec file, or default."""
    if getattr(args, "pack", None):
        pack = get_pack(args.pack)
        return parse_spec(pack.spec_text), pack.spec_text, pack.scenarios()
    spec_text = Path(args.spec).read_text() if args.spec else DEFAULT_SPEC
    spec = parse_spec(spec_text)
    return spec, spec_text, generate_scenarios(spec)


def cmd_gate(args: argparse.Namespace) -> int:
    """One-shot CI gate: build -> optional autofix -> enforce a score threshold.

    Designed to be the body of the AgentProof GitHub Action. Writes a Markdown
    summary to $GITHUB_STEP_SUMMARY when present, and a badge when asked.
    """
    spec, spec_text, scenarios = _resolve_spec_and_scenarios(args)
    graph = synthesize(spec)
    fixes = []
    if args.autofix:
        results = run_suite(graph, spec, scenarios)
        report = autofix(graph, spec, results)
        graph, fixes = report.graph, report.fixes

    results = run_suite(graph, spec, scenarios)
    coverage = compute_coverage(graph, results)
    score = compute_score(results, coverage)
    policy = policy_summary(graph, spec)
    passed = sum(1 for r in results if r.passed)
    total = len(results)

    print(f"{_c(BOLD, spec.name)}  ·  gate threshold {args.fail_under}")
    _print_results(results, coverage, score)
    if fixes:
        print(f"  auto-fixes applied: {len(fixes)}")

    failures = []
    if score.overall < args.fail_under:
        failures.append(f"Agent Score {score.overall} below threshold {args.fail_under}")
    if passed < total:
        failures.append(f"{total - passed} scenario(s) failing")
    if policy["open"]:
        failures.append(f"{policy['open']} policy line(s) open")

    _write_step_summary(spec, score, passed, total, policy, fixes, failures)
    if args.badge:
        Path(args.badge).write_text(score_badge(score))
        print(f"  badge: {_c(CYAN, args.badge)}")

    if failures:
        print(_c(RED, "\nGATE FAILED:"))
        for f in failures:
            print(f"  {_c(RED, '✗')} {f}")
        return 1
    print(_c(GREEN, "\n✓ GATE PASSED"))
    return 0


def _write_step_summary(spec, score, passed, total, policy, fixes, failures) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    verdict = "✅ **SHIPPABLE**" if score.shippable and not failures else "🚫 **BLOCKED**"
    lines = [
        f"## ⚡ AgentProof gate — {spec.name}",
        "",
        f"{verdict} · Agent Score **{score.overall}/100**",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Tests | {passed}/{total} |",
        f"| Safety | {score.safety} |",
        f"| Reliability | {score.reliability} |",
        f"| Coverage | {score.coverage} |",
        f"| Policy lines | {policy['satisfied']}/{policy['total']} satisfied |",
        f"| Auto-fixes applied | {len(fixes)} |",
    ]
    if failures:
        lines += ["", "### Gate failures", *[f"- {f}" for f in failures]]
    try:
        with open(summary_path, "a") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


def cmd_serve(args: argparse.Namespace) -> int:
    from agentproof.server import serve as serve_backend

    serve_backend(args.data_dir, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Scaffold AgentProof into an existing repo: spec + CI workflow."""
    target = Path(args.dir)
    spec_path = target / "agent.spec.md"
    if not spec_path.exists() or args.force:
        spec_path.write_text(DEFAULT_SPEC)
        print(f"  wrote {_c(CYAN, str(spec_path))}")
    else:
        print(f"  {_c(YELLOW, 'kept existing')} {spec_path} (use --force to overwrite)")
    wf_dir = target / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf = wf_dir / "agentproof.yml"
    wf.write_text(_AGENTPROOF_WORKFLOW)
    print(f"  wrote {_c(CYAN, str(wf))}")
    print(f"\n{_c(BOLD, 'AgentProof is wired in.')} Edit agent.spec.md, then:")
    print(f"  {_c(BOLD, 'agentproof gate --spec agent.spec.md --autofix')}")
    return 0


_AGENTPROOF_WORKFLOW = """name: agentproof
on: [push, pull_request]
jobs:
  behavior-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: evanl666/agentproof@main
        with:
          spec: agent.spec.md
          fail-under: '85'
          autofix: 'true'
"""


def cmd_run(args: argparse.Namespace) -> int:
    """Run the verified agent live against a message — no export needed."""
    from agentproof.runtime import AgentRuntime, ClaudePlanner, LocalPlanner, default_planner

    project = Path(args.project)
    spec, graph = _load_spec(project), _load_graph(project)
    if args.model:
        planner = ClaudePlanner(model=args.model)
    elif args.local:
        planner = LocalPlanner()
    else:
        planner = default_planner()
    runtime = AgentRuntime(graph, spec, planner=planner)
    result = runtime.run(args.message, approved_by_human=args.approve)

    print(f"{_c(BOLD, 'user')}: {args.message}")
    for step in result.trace:
        icon = {"guard": "🛡", "condition": "⚖", "approval": "✋", "tool": "🔧",
                "planner": "🧠", "responder": "✍", "input": "→", "output": "✓"}.get(step.kind, "·")
        print(f"  {icon} {_c(DIM, step.node_id)}: {step.detail}")
    print(f"{_c(BOLD, 'agent')}: {result.reply}")
    tags = []
    if result.flagged_injection:
        tags.append(_c(YELLOW, "injection-quarantined") if any(s.kind == "guard" for s in result.trace) else _c(RED, "injection-LEAKED"))
    if result.redacted_pii:
        tags.append(_c(GREEN, "pii-redacted"))
    if result.approval_required:
        tags.append(_c(CYAN, "approval-required"))
    if result.actions:
        tags.append("actions: " + ", ".join(result.actions))
    if tags:
        print("  " + " · ".join(tags))
    print(f"  {_c(DIM, 'planner: ' + result.planner)}")
    return 1 if result.blocked and args.check else 0


def cmd_prove(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph = _load_spec(project), _load_graph(project)
    proofs = prove(graph, spec)
    print(_c(BOLD, "Static reachability proofs:"))
    failing = 0
    for p in proofs:
        mark = _c(GREEN, "✓ PROVEN") if p.holds else _c(RED, "✗ VIOLATED")
        print(f"  {mark}  {p.property}")
        print(f"        {_c(DIM, p.detail)}")
        if not p.holds:
            failing += 1
            print(f"        {_c(RED, 'counterexample:')} {' → '.join(p.counterexample)}")
    verdict = (
        _c(GREEN, "all safety properties proven")
        if failing == 0
        else _c(RED, f"{failing} propert{'y' if failing == 1 else 'ies'} VIOLATED")
    )
    print(f"\n  {verdict}")
    return 1 if failing and args.check else 0


def cmd_replay(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph = _load_spec(project), _load_graph(project)
    scenarios = traces_to_scenarios(args.traces, spec)
    if not scenarios:
        print(_c(YELLOW, "No user messages found in the traces."))
        return 0
    results = run_suite(graph, spec, scenarios)
    coverage = compute_coverage(graph, results)
    score = compute_score(results, coverage)
    print(f"{_c(BOLD, 'Production replay')} · {len(scenarios)} real messages from {args.traces}\n")
    _print_results(results, coverage, score)
    if args.save:
        existing = _load_scenarios(project)
        _save(project, scenarios=[s.to_dict() for s in existing + scenarios])
        print(f"\n  {_c(GREEN, 'Saved')} {len(scenarios)} real-traffic scenarios into the project's suite")
    failed = sum(1 for r in results if not r.passed)
    return 1 if failed and args.check else 0


def cmd_redteam(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph = _load_spec(project), _load_graph(project)
    using_model = args.model or ClaudeRedTeam.available()
    print(f"{_c(BOLD, 'Red-team')} · {'model-driven (' + (args.model or 'claude-haiku-4-5') + ')' if using_model else 'offline template generator'}")
    scenarios = redteam_scenarios(spec, n=args.n, model=args.model)
    results = run_suite(graph, spec, scenarios)
    _print_results(results, compute_coverage(graph, results), compute_score(results, compute_coverage(graph, results)))
    if args.save:
        existing = _load_scenarios(project)
        _save(project, scenarios=[s.to_dict() for s in existing + scenarios])
        print(f"\n  {_c(GREEN, 'Saved')} {len(scenarios)} red-team scenarios into the project's suite")
    failed = sum(1 for r in results if not r.passed)
    return 1 if failed and args.check else 0


def cmd_middleware(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec = _load_spec(project)
    path = export_middleware(spec, args.out)
    print(f"Runtime guard middleware written to {_c(CYAN, str(path))}")
    print("  Vendor it into any agent: from guards import is_injection, authorize_spend, redact_pii")
    return 0


def cmd_playground(args: argparse.Namespace) -> int:
    path = write_playground(args.out)
    print(f"Shareable playground written to {_c(CYAN, str(path))}")
    print("  Open it in a browser or drop it in a gist — self-contained, no install.")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Black-box test a real, running agent over HTTP — no migration."""
    project = Path(args.project) if args.project else None
    if args.spec:
        spec = parse_spec(Path(args.spec).read_text())
        scenarios = generate_scenarios(spec, seed=args.seed, size=args.size)
    elif project and (project / "spec.json").exists():
        spec, scenarios = _load_spec(project), _load_scenarios(project)
    else:
        spec = parse_spec(DEFAULT_SPEC)
        scenarios = generate_scenarios(spec, seed=args.seed, size=args.size)
    caller = http_agent(args.url, field_in=args.field_in, field_out=args.field_out)
    from agentproof.intelligence import use_llm

    judge = None
    if use_llm() and not args.no_llm:
        from agentproof.smart import SmartJudge

        judge = SmartJudge()
    kind = "🧠 LLM judge" if judge else "pattern detector"
    print(f"{_c(BOLD, 'Black-box probe')} · {len(scenarios)} scenarios → {args.url}  ({kind})\n")
    results = probe_agent(caller, spec, scenarios, judge=judge)
    summary = probe_summary(results)
    for r in results:
        if not r.passed:
            tag = _c(RED, "✗") if r.violations else _c(YELLOW, "!")
            detail = r.violations[0] if r.violations else (r.error or "failed")
            print(f"  {tag} {r.scenario.id} [{r.scenario.category.value}]: {detail}")
    verdict = (
        _c(GREEN, f"PASSED {summary['passed']}/{summary['total']}")
        if summary["failed"] == 0
        else _c(RED, f"{summary['failed']} real-response violation(s) / {summary['total']}")
    )
    print(f"\n  {verdict}")
    return 1 if summary["failed"] and args.check else 0


def cmd_sandbox(args: argparse.Namespace) -> int:
    """Run the AgentWorld fake SaaS sandbox for agents to act in safely."""
    AgentWorld().serve(port=args.port)
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    """Compile an OpenAPI spec into safe agent tools (preview/commit/undo/approve)."""
    import json as _json

    data = _json.loads(Path(args.openapi).read_text())
    written = compile_to_repo(data, args.out, name=args.name)
    print(f"{_c(BOLD, 'Safe tools compiled from')} {args.openapi} → {args.out}")
    for p in written:
        print(f"  {p}")
    return 0


def cmd_infer(args: argparse.Namespace) -> int:
    """Scan an existing agent and infer a starter spec + risk report."""
    graph = import_agent(args.agent)
    spec, markdown, risk = infer_from_graph(graph)
    Path(args.out).write_text(markdown)
    print(f"{_c(BOLD, 'Inferred spec')} from {args.agent} → {_c(CYAN, args.out)}\n")
    print(_c(DIM, markdown))
    return 0


def cmd_movie(args: argparse.Namespace) -> int:
    project = Path(args.project)
    spec, graph = _load_spec(project), _load_graph(project)
    path = write_proof_movie(args.out, graph, spec)
    print(f"Counterexample replay movie written to {_c(CYAN, str(path))}")
    return 0


def cmd_mutate(args: argparse.Namespace) -> int:
    from agentproof.mutation import mutation_test

    project = Path(args.project)
    spec, graph, scenarios = _load_spec(project), _load_graph(project), _load_scenarios(project)
    report = mutation_test(graph, spec, scenarios)
    print(f"{_c(BOLD, 'Mutation testing')} · {report.to_dict()['killed']}/{report.to_dict()['total']} mutants killed")
    print(f"  mutation score: {_c(GREEN if report.score >= 0.9 else YELLOW, f'{report.score*100:.0f}%')}")
    for m in report.survivors:
        print(f"  {_c(RED, '✗ survived')} {m.description} — a regression your tests wouldn't catch")
    return 1 if report.survivors and args.check else 0


def cmd_coverage2(args: argparse.Namespace) -> int:
    from agentproof.coverage2 import compute_risk_coverage

    project = Path(args.project)
    spec, graph, scenarios = _load_spec(project), _load_graph(project), _load_scenarios(project)
    rc = compute_risk_coverage(graph, run_suite(graph, spec, scenarios))
    print(_c(BOLD, "Risk coverage 2.0:"))
    print(f"  high-risk tools attacked : {round(rc.high_risk_tool_coverage*100)}%")
    print(f"  sensitive→external flows : {round(rc.data_flow_coverage*100)}%")
    print(f"  approval paths exercised : {round(rc.approval_path_coverage*100)}%")
    print(f"  fallback paths exercised : {round(rc.fallback_coverage*100)}%")
    for t in rc.uncovered_high_risk_tools:
        print(f"  {_c(RED, '✗')} high-risk tool never attacked: {t}")
    return 0


def cmd_compliance(args: argparse.Namespace) -> int:
    from agentproof.compliance import write_compliance

    project = Path(args.project)
    spec, graph, scenarios = _load_spec(project), _load_graph(project), _load_scenarios(project)
    path = write_compliance(args.out, spec, graph, scenarios, fmt=args.format)
    print(f"Compliance report written to {_c(CYAN, str(path))}")
    return 0


def cmd_incident(args: argparse.Namespace) -> int:
    from agentproof.incident import incidents_to_regressions, regression_pr_body

    project = Path(args.project)
    spec = _load_spec(project)
    incidents = incidents_to_regressions(args.incidents, spec)
    if not incidents:
        print(_c(YELLOW, "No incident inputs found."))
        return 0
    print(f"{_c(BOLD, f'{len(incidents)} incident(s) → regression scenarios')}")
    for inc in incidents:
        print(f"  [{inc.suspected_kind}] {inc.title[:50]}: {inc.message[:50]}")
    if args.save:
        existing = _load_scenarios(project)
        _save(project, scenarios=[s.to_dict() for s in existing + [i.scenario for i in incidents]])
        print(f"\n  {_c(GREEN, 'Saved')} into the project's suite")
    if args.pr_body:
        Path(args.pr_body).write_text(regression_pr_body(incidents, project=str(project)))
        print(f"  PR body: {_c(CYAN, args.pr_body)}")
    return 0


def cmd_review_pr(args: argparse.Namespace) -> int:
    from agentproof.prbot import build_review_comment, verdict_blocks

    project = Path(args.project)
    spec = _load_spec(project)
    before = _load_graph(project, "baseline_graph")
    after = _load_graph(project)
    scenarios = _load_scenarios(project)
    comment = build_review_comment(spec, before, after, scenarios)
    if args.out:
        Path(args.out).write_text(comment)
    print(comment)
    return 1 if verdict_blocks(comment) and args.check else 0


def cmd_packs_publish(args: argparse.Namespace) -> int:
    from agentproof.marketplace import publish_pack

    spec_text = Path(args.spec).read_text()
    entry = publish_pack(args.name, spec_text, description=args.description or "",
                         domain=args.domain, version=args.version)
    print(f"Published {_c(BOLD, entry.name + '@' + entry.version)} to the registry")
    return 0


def cmd_packs_install(args: argparse.Namespace) -> int:
    from agentproof.marketplace import install_pack

    path = install_pack(args.name, args.out, version=args.version)
    print(f"Installed {_c(BOLD, args.name)} → {_c(CYAN, str(path))}")
    return 0


def cmd_packs_search(args: argparse.Namespace) -> int:
    from agentproof.marketplace import search_packs

    entries = search_packs(args.query or "")
    if not entries:
        print(_c(DIM, "No published packs yet. Use `agentproof packs-publish`."))
        return 0
    print(_c(BOLD, "Registry packs:"))
    for e in entries:
        print(f"  {_c(CYAN, e.name + '@' + e.version):<28} [{e.domain}] {e.description}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """Autonomous multi-turn security audit — point it at an agent, get a report."""
    from agentproof.attack import http_multiturn_agent, runtime_agent
    from agentproof.audit import audit_agent, audit_spec, render_markdown, write_audit
    from agentproof.runtime import AgentRuntime, default_planner

    project_graph = None
    if args.pack:
        spec = get_pack(args.pack).spec()
    elif args.spec:
        spec = parse_spec(Path(args.spec).read_text())
    elif args.project:
        spec = _load_spec(Path(args.project))
        project_graph = _load_graph(Path(args.project))  # audit the project's actual (fixed) agent
    else:
        spec = parse_spec(DEFAULT_SPEC)

    if args.url:
        agent = http_multiturn_agent(args.url, field_in=args.field_in)
        name = args.url
    else:
        if project_graph is not None:
            graph = project_graph
        else:
            from agentproof.intelligence import smart_synthesize
            graph = smart_synthesize(spec)
        agent = runtime_agent(AgentRuntime(graph, spec, planner=default_planner()))
        name = spec.name

    print(f"{_c(BOLD, '🔒 Autonomous security audit')} · {name}  (adaptive multi-turn red-team)\n")
    report = audit_agent(agent, spec, max_turns=args.turns, model=args.model, agent_name=name)
    print(f"  Verdict: {_c(RED, report.verdict) if report.breached else _c(GREEN, report.verdict)}")
    print(f"  {_c(DIM, report.summary[:400])}")
    for f in sorted(report.findings, key=lambda x: not x.succeeded):
        icon = _c(RED, "🔴 BREACHED") if f.succeeded else _c(GREEN, "🟢 held")
        extra = f" in {f.turns_to_break} turn(s) → {f.suggested_fix}" if f.succeeded else ""
        print(f"  {icon} [{f.severity}] {f.goal[:60]}{extra}")
    if args.out:
        write_audit(args.out, report, fmt=args.format)
        print(f"\n  report: {_c(CYAN, args.out)}")
    return 1 if report.breached and args.check else 0


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
    p.add_argument("--pack", choices=sorted(p_.id for p_ in list_packs()),
                   help="start from a domain scenario pack")
    p.add_argument("--no-llm", action="store_true",
                   help="force the deterministic rule-based path (default is LLM-native when a key is present)")
    p.add_argument("--smart-scenarios", action="store_true",
                   help="also generate adversarial scenarios with the LLM (creative, non-deterministic)")
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

    p = sub.add_parser("export", help="export a production agent repo")
    p.add_argument("project")
    p.add_argument("-o", "--out", default="./exported-agent")
    p.add_argument("-t", "--target", choices=sorted(EXPORTERS), default="langgraph",
                   help="framework to export (langgraph/openai/crewai/typescript)")
    p.add_argument("--force", action="store_true", help="export even with failing scenarios")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("packs", help="list domain scenario packs")
    p.set_defaults(fn=cmd_packs)

    p = sub.add_parser("cost", help="project agent cost across models")
    p.add_argument("project")
    p.add_argument("--model", choices=sorted(MODEL_PRICES), default="claude-sonnet-5")
    p.set_defaults(fn=cmd_cost)

    p = sub.add_parser("policy", help="visualize policy lines drawn on the graph")
    p.add_argument("project")
    p.add_argument("--check", action="store_true", help="exit 1 if any policy line is open")
    p.set_defaults(fn=cmd_policy)

    p = sub.add_parser("commit", help="snapshot the current behavior (team mode)")
    p.add_argument("project")
    p.add_argument("-m", "--message", default="", help="snapshot message")
    p.add_argument("--author", default="unknown")
    p.set_defaults(fn=cmd_commit)

    p = sub.add_parser("review", help="PR-style behavior review between two snapshots")
    p.add_argument("project")
    p.add_argument("base", nargs="?", type=int, default=None, help="base version")
    p.add_argument("head", nargs="?", type=int, default=None, help="head version")
    p.add_argument("--check", action="store_true", help="exit 1 if the verdict is block")
    p.set_defaults(fn=cmd_review)

    p = sub.add_parser("badge", help="render an Agent Score SVG badge for your README")
    p.add_argument("project")
    p.add_argument("-o", "--out", default="agentproof-badge.svg")
    p.add_argument("--label", default="agentproof")
    p.set_defaults(fn=cmd_badge)

    p = sub.add_parser("gate", help="one-shot CI gate (build/fix/enforce score threshold)")
    p.add_argument("spec", nargs="?", help="spec file; default example")
    p.add_argument("--pack", choices=sorted(p_.id for p_ in list_packs()),
                   help="use a domain scenario pack instead of a spec file")
    p.add_argument("--fail-under", type=int, default=85, dest="fail_under",
                   help="minimum Agent Score to pass (default 85)")
    p.add_argument("--autofix", action="store_true", help="auto-repair before scoring")
    p.add_argument("--badge", help="also write an SVG score badge to this path")
    p.set_defaults(fn=cmd_gate)

    p = sub.add_parser("init", help="scaffold AgentProof into an existing repo")
    p.add_argument("--dir", default=".")
    p.add_argument("--force", action="store_true", help="overwrite an existing spec")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("serve", help="launch the multi-project team backend + dashboard")
    p.add_argument("--data-dir", default=".agentproof-server")
    p.add_argument("--port", type=int, default=4600)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("prove", help="static reachability proofs (no path bypasses a gate)")
    p.add_argument("project")
    p.add_argument("--check", action="store_true", help="exit 1 if any property is violated")
    p.set_defaults(fn=cmd_prove)

    p = sub.add_parser("probe", help="black-box test a real running agent over HTTP (no migration)")
    p.add_argument("url", help="the agent's chat endpoint, e.g. http://localhost:8000/chat")
    p.add_argument("project", nargs="?", help="project to source scenarios/spec from")
    p.add_argument("--spec", help="behavior spec to test against (else the project's, else default)")
    p.add_argument("--field-in", default="message", help="request JSON field for the user message")
    p.add_argument("--field-out", help="response JSON field to read the reply from")
    p.add_argument("--no-llm", action="store_true", help="use the pattern detector instead of the LLM judge")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--size", type=int, default=50)
    p.add_argument("--check", action="store_true", help="exit 1 on any real-response violation")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("sandbox", help="run the AgentWorld fake SaaS sandbox (Stripe/Gmail/Postgres/...)")
    p.add_argument("--port", type=int, default=4700)
    p.set_defaults(fn=cmd_sandbox)

    p = sub.add_parser("compile", help="compile an OpenAPI spec into safe agent tools")
    p.add_argument("openapi", help="OpenAPI JSON file")
    p.add_argument("-o", "--out", default="./safe-tools")
    p.add_argument("--name", default="safe-tools")
    p.set_defaults(fn=cmd_compile)

    p = sub.add_parser("infer", help="scan an existing agent and infer a starter spec")
    p.add_argument("agent", help="agent file (any of the 7 supported formats)")
    p.add_argument("-o", "--out", default="agent.spec.md")
    p.set_defaults(fn=cmd_infer)

    p = sub.add_parser("movie", help="render a counterexample replay movie of proof violations")
    p.add_argument("project")
    p.add_argument("-o", "--out", default="counterexample.html")
    p.set_defaults(fn=cmd_movie)

    p = sub.add_parser("replay", help="replay production traces as regression scenarios")
    p.add_argument("traces", help="traces file (JSONL / LangSmith JSON / OTel spans)")
    p.add_argument("project")
    p.add_argument("--save", action="store_true", help="add the real-traffic scenarios to the project")
    p.add_argument("--check", action="store_true", help="exit 1 on failures")
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("redteam", help="generate novel adversarial scenarios (model-driven or offline)")
    p.add_argument("project")
    p.add_argument("-n", type=int, default=12, help="how many attacks to generate")
    p.add_argument("--model", help="use a real Claude model to invent attacks (e.g. claude-haiku-4-5)")
    p.add_argument("--save", action="store_true", help="add the red-team scenarios to the project")
    p.add_argument("--check", action="store_true", help="exit 1 on failures")
    p.set_defaults(fn=cmd_redteam)

    p = sub.add_parser("middleware", help="export runtime guard middleware to vendor into any agent")
    p.add_argument("project")
    p.add_argument("-o", "--out", default="guards.py")
    p.set_defaults(fn=cmd_middleware)

    p = sub.add_parser("playground", help="build a shareable self-contained HTML playground")
    p.add_argument("-o", "--out", default="agentproof-playground.html")
    p.set_defaults(fn=cmd_playground)

    p = sub.add_parser("run", help="run the verified agent live against a message (no export)")
    p.add_argument("project")
    p.add_argument("-m", "--message", required=True, help="the user message to run")
    p.add_argument("--model", help="use a real Claude model as the planner (e.g. claude-haiku-4-5)")
    p.add_argument("--local", action="store_true", help="force the deterministic offline planner")
    p.add_argument("--approve", action="store_true", help="simulate a human approving the action")
    p.add_argument("--check", action="store_true", help="exit 1 if the run was blocked")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("mutate", help="mutation testing — does your suite kill injected regressions?")
    p.add_argument("project")
    p.add_argument("--check", action="store_true", help="exit 1 if any mutant survives")
    p.set_defaults(fn=cmd_mutate)

    p = sub.add_parser("coverage", help="risk coverage 2.0 (high-risk tools / data-flows / approvals)")
    p.add_argument("project")
    p.set_defaults(fn=cmd_coverage2)

    p = sub.add_parser("compliance", help="generate a compliance report (controls/tests/proofs/gaps)")
    p.add_argument("project")
    p.add_argument("-o", "--out", default="compliance.md")
    p.add_argument("--format", choices=["md", "html"], default="md")
    p.set_defaults(fn=cmd_compliance)

    p = sub.add_parser("incident", help="turn production incidents into regression scenarios")
    p.add_argument("incidents", help="incident/Sentry JSON export")
    p.add_argument("project")
    p.add_argument("--save", action="store_true", help="add the regressions to the project")
    p.add_argument("--pr-body", help="write a PR body describing the fix")
    p.set_defaults(fn=cmd_incident)

    p = sub.add_parser("review-pr", help="render a PR behavior-review comment (Codecov for agents)")
    p.add_argument("project")
    p.add_argument("-o", "--out", help="write the Markdown comment to a file")
    p.add_argument("--check", action="store_true", help="exit 1 on a changes-requested verdict")
    p.set_defaults(fn=cmd_review_pr)

    p = sub.add_parser("packs-publish", help="publish a spec as a reusable pack to the registry")
    p.add_argument("spec")
    p.add_argument("--name", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--domain", default="general")
    p.add_argument("--version", default="1.0.0")
    p.set_defaults(fn=cmd_packs_publish)

    p = sub.add_parser("packs-install", help="install a registry pack into a project")
    p.add_argument("name")
    p.add_argument("-o", "--out", default="./agentproof-project")
    p.add_argument("--version")
    p.set_defaults(fn=cmd_packs_install)

    p = sub.add_parser("packs-search", help="search the pack registry")
    p.add_argument("query", nargs="?")
    p.set_defaults(fn=cmd_packs_search)

    p = sub.add_parser("audit", help="autonomous multi-turn security audit (AI red-teams your agent)")
    p.add_argument("url", nargs="?", help="a live agent endpoint to audit; omit to audit a synthesized agent")
    p.add_argument("--pack", choices=sorted(p_.id for p_ in list_packs()), help="audit a domain pack's agent")
    p.add_argument("--spec", help="behavior spec to audit against")
    p.add_argument("--project", help="project to source the spec from")
    p.add_argument("--field-in", default="message", help="request JSON field for the message")
    p.add_argument("--turns", type=int, default=6, help="max turns per attack campaign")
    p.add_argument("--model", help="attacker/judge model (default: cheapest, Haiku)")
    p.add_argument("-o", "--out", help="write the audit report to a file")
    p.add_argument("--format", choices=["md", "html"], default="md")
    p.add_argument("--check", action="store_true", help="exit 1 if any attack succeeds")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("studio", help="launch the local visual IDE")
    p.add_argument("--dir", default=".")
    p.add_argument("--port", type=int, default=4517)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_studio)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except FileNotFoundError as exc:
        target = getattr(args, "project", None) or exc.filename
        print(_c(RED, f"Error: no AgentProof project at {target!r}."), file=sys.stderr)
        print("Run `agentproof build <spec>` (or `demo -o <dir>`) first.", file=sys.stderr)
        return 2
    except KeyError as exc:
        print(_c(RED, f"Error: {exc.args[0] if exc.args else exc}"), file=sys.stderr)
        return 2
    except (ValueError, json.JSONDecodeError) as exc:
        print(_c(RED, f"Error: {exc}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
