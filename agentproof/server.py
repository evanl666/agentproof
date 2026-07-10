"""AgentProof hosted backend: multi-project collaboration server.

Where `studio` is a single-project IDE, `server` is the team platform: many
projects side by side on one dashboard, each with its live Agent Score and
shippable verdict, plus an org-wide policy library teams can attach to any
project so "PII may never leave the system" is defined once and enforced
everywhere.

It is still zero-dependency (Python stdlib only) and file-backed — every
project and policy set is a JSON document under a data directory you can commit
or mount. Run it locally, in a container, or behind your own auth proxy.
"""

from __future__ import annotations

import json
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agentproof.autofix import autofix
from agentproof.coverage import compute_coverage
from agentproof.graph import AgentGraph
from agentproof.packs import get_pack
from agentproof.policy_lines import policy_summary
from agentproof.report import CANVAS_CSS
from agentproof.scenarios import Scenario, generate_scenarios
from agentproof.score import compute_score
from agentproof.simulator import run_suite
from agentproof.spec import BehaviorSpec, Constraint, ConstraintKind, parse_spec
from agentproof.synthesis import synthesize

_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG.sub("-", name.lower()).strip("-") or "project"


class ProjectStore:
    """File-backed store for many projects and a shared policy library."""

    def __init__(self, data_dir: str | Path = ".agentproof-server"):
        self.data_dir = Path(data_dir)
        (self.data_dir / "projects").mkdir(parents=True, exist_ok=True)
        self._policy_file = self.data_dir / "policy_library.json"

    # -- projects --------------------------------------------------------

    def _project_path(self, project_id: str) -> Path:
        return self.data_dir / "projects" / f"{project_id}.json"

    def list_projects(self) -> list[dict[str, Any]]:
        out = []
        for path in sorted((self.data_dir / "projects").glob("*.json")):
            data = json.loads(path.read_text())
            out.append(self._summary(data))
        return sorted(out, key=lambda p: p["name"].lower())

    def _summary(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": data["id"],
            "name": data["name"],
            "score": data.get("score"),
            "passed": data.get("passed"),
            "total": data.get("total"),
            "shippable": (data.get("score") or {}).get("shippable", False),
            "updated_at": data.get("updated_at", 0),
        }

    def create_project(
        self,
        name: str,
        spec_text: str | None = None,
        pack: str | None = None,
        policy_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if pack:
            pack_obj = get_pack(pack)
            spec = pack_obj.spec()
            spec_text = pack_obj.spec_text
            scenarios = pack_obj.scenarios()
        else:
            spec_text = spec_text or ""
            spec = parse_spec(spec_text)
            scenarios = generate_scenarios(spec)
        # Attach org policy sets from the library.
        for constraint in self._resolve_policies(policy_ids or []):
            if not any(c.kind == constraint.kind for c in spec.constraints):
                spec.constraints.append(constraint)
        graph = synthesize(spec)
        project_id = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
        record = self._materialize(project_id, name, spec, graph, scenarios, spec_text, policy_ids or [])
        self._save(record)
        return record

    def get_project(self, project_id: str) -> dict[str, Any]:
        path = self._project_path(project_id)
        if not path.exists():
            raise KeyError(project_id)
        return json.loads(path.read_text())

    def _save(self, record: dict[str, Any]) -> None:
        self._project_path(record["id"]).write_text(json.dumps(record, indent=2))

    def _load_objects(self, record: dict[str, Any]):
        spec = BehaviorSpec.from_dict(record["spec"])
        graph = AgentGraph.from_dict(record["graph"])
        scenarios = [Scenario.from_dict(s) for s in record["scenarios"]]
        return spec, graph, scenarios

    def _materialize(
        self, project_id, name, spec, graph, scenarios, spec_text, policy_ids
    ) -> dict[str, Any]:
        import time

        results = run_suite(graph, spec, scenarios)
        coverage = compute_coverage(graph, results)
        score = compute_score(results, coverage)
        return {
            "id": project_id,
            "name": name,
            "spec_text": spec_text,
            "spec": spec.to_dict(),
            "graph": graph.to_dict(),
            "scenarios": [s.to_dict() for s in scenarios],
            "results": [r.to_dict() for r in results],
            "coverage": coverage.to_dict(),
            "score": score.to_dict(),
            "policy": policy_summary(graph, spec),
            "policy_ids": policy_ids,
            "passed": sum(1 for r in results if r.passed),
            "total": len(results),
            "updated_at": time.time(),
        }

    def simulate(self, project_id: str) -> dict[str, Any]:
        record = self.get_project(project_id)
        spec, graph, scenarios = self._load_objects(record)
        updated = self._materialize(
            project_id, record["name"], spec, graph, scenarios,
            record["spec_text"], record.get("policy_ids", []),
        )
        self._save(updated)
        return updated

    def autofix(self, project_id: str) -> dict[str, Any]:
        record = self.get_project(project_id)
        spec, graph, scenarios = self._load_objects(record)
        results = run_suite(graph, spec, scenarios)
        report = autofix(graph, spec, results)
        updated = self._materialize(
            project_id, record["name"], spec, report.graph, scenarios,
            record["spec_text"], record.get("policy_ids", []),
        )
        updated["fixes"] = [f.to_dict() for f in report.fixes]
        self._save(updated)
        return updated

    def run_project(self, project_id: str, message: str, approved: bool = False) -> dict[str, Any]:
        from agentproof.runtime import AgentRuntime, default_planner

        record = self.get_project(project_id)
        spec, graph, _ = self._load_objects(record)
        runtime = AgentRuntime(graph, spec, planner=default_planner())
        return runtime.run(message, approved_by_human=approved).to_dict()

    def delete_project(self, project_id: str) -> None:
        path = self._project_path(project_id)
        if path.exists():
            path.unlink()

    # -- policy library --------------------------------------------------

    def _read_library(self) -> list[dict[str, Any]]:
        if self._policy_file.exists():
            return json.loads(self._policy_file.read_text())
        return []

    def list_policies(self) -> list[dict[str, Any]]:
        return self._read_library()

    def add_policy(self, name: str, constraints: list[dict[str, Any]]) -> dict[str, Any]:
        library = self._read_library()
        entry = {
            "id": f"{_slug(name)}-{uuid.uuid4().hex[:4]}",
            "name": name,
            "constraints": constraints,
        }
        library.append(entry)
        self._policy_file.write_text(json.dumps(library, indent=2))
        return entry

    def _resolve_policies(self, policy_ids: list[str]) -> list[Constraint]:
        by_id = {p["id"]: p for p in self._read_library()}
        constraints: list[Constraint] = []
        for pid in policy_ids:
            entry = by_id.get(pid)
            if not entry:
                continue
            for c in entry["constraints"]:
                constraints.append(Constraint.from_dict(c))
        return constraints


# ---------------------------------------------------------------------------
# Dashboard UI
# ---------------------------------------------------------------------------

def _dashboard_html() -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>AgentProof · Team dashboard</title>
<style>{CANVAS_CSS}
.wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }}
.card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
.card h3 {{ font-size: 15px; margin-bottom: 4px; }}
.card .id {{ font-size: 11px; color: var(--muted); margin-bottom: 12px; }}
.ring {{ font-size: 30px; font-weight: 700; }}
.row {{ display: flex; gap: 8px; align-items: center; margin: 8px 0; flex-wrap: wrap; }}
.toolbar {{ display: flex; gap: 8px; margin: 16px 0; flex-wrap: wrap; }}
input, select {{ background: #0d1117; color: var(--text); border: 1px solid var(--border);
  border-radius: 6px; padding: 8px 10px; font-size: 13px; }}
button {{ background: #238636; color: #fff; border: 1px solid #2ea043; border-radius: 6px;
  padding: 8px 14px; font-size: 13px; cursor: pointer; }}
button.ghost {{ background: #21262d; border-color: var(--border); }}
a {{ color: var(--blue); text-decoration: none; }}
.empty {{ color: var(--muted); padding: 40px; text-align: center; }}
</style></head>
<body>
<div class="wrap">
  <header style="border:none;padding:0">
    <h1>⚡ AgentProof · Team dashboard</h1>
  </header>
  <div class="toolbar">
    <input id="name" placeholder="New project name">
    <select id="pack">
      <option value="">— blank spec —</option>
      <option value="support">support pack</option>
      <option value="fintech">fintech pack</option>
      <option value="healthcare">healthcare pack</option>
    </select>
    <button id="create">Create project</button>
    <button id="refresh" class="ghost">Refresh</button>
  </div>
  <div id="cards" class="cards"></div>
</div>
<script>
const $ = (id) => document.getElementById(id);

function color(score) {{
  if (!score) return 'var(--muted)';
  if (score >= 90) return 'var(--green)';
  if (score >= 75) return 'var(--amber)';
  return 'var(--red)';
}}

async function api(path, body) {{
  const res = await fetch(path, body ? {{
    method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify(body)
  }} : undefined);
  return res.json();
}}

async function load() {{
  const projects = await api('/api/projects');
  const cards = $('cards');
  if (!projects.length) {{
    cards.innerHTML = '<div class="empty">No projects yet. Create one above or pick a domain pack.</div>';
    return;
  }}
  cards.innerHTML = projects.map((p) => {{
    const s = p.score || {{}};
    const verdict = p.shippable
      ? '<span class="chip good"><b>✓ shippable</b></span>'
      : '<span class="chip bad"><b>✗ not shippable</b></span>';
    return `<div class="card">
      <h3>${{p.name}}</h3>
      <div class="id">${{p.id}}</div>
      <div class="row">
        <span class="ring" style="color:${{color(s.overall)}}">${{s.overall ?? '—'}}</span>
        <span class="muted">/ 100</span>
        ${{verdict}}
      </div>
      <div class="row">
        <span class="chip"><b>${{p.passed ?? '—'}}/${{p.total ?? '—'}}</b> tests</span>
        <span class="chip"><b>${{s.safety ?? '—'}}</b> safety</span>
      </div>
      <div class="row">
        <button class="ghost" onclick="fixProject('${{p.id}}')">Auto-fix</button>
        <button class="ghost" onclick="delProject('${{p.id}}')">Delete</button>
      </div>
    </div>`;
  }}).join('');
}}

async function fixProject(id) {{ await api('/api/projects/' + id + '/autofix', {{}}); load(); }}
async function delProject(id) {{ await fetch('/api/projects/' + id, {{ method: 'DELETE' }}); load(); }}

$('create').addEventListener('click', async () => {{
  const name = $('name').value.trim();
  if (!name) return;
  await api('/api/projects', {{ name, pack: $('pack').value || null }});
  $('name').value = '';
  load();
}});
$('refresh').addEventListener('click', load);
load();
</script>
</body></html>"""


def make_handler(store: ProjectStore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, payload: Any, content_type: str = "application/json") -> None:
            body = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self._send(200, _dashboard_html(), "text/html")
            if self.path == "/api/projects":
                return self._send(200, store.list_projects())
            if self.path == "/api/policies":
                return self._send(200, store.list_policies())
            m = re.match(r"^/api/projects/([^/]+)$", self.path)
            if m:
                try:
                    return self._send(200, store.get_project(m.group(1)))
                except KeyError:
                    return self._send(404, {"error": "not found"})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                body = self._body()
            except json.JSONDecodeError:
                return self._send(400, {"error": "invalid JSON"})
            try:
                if self.path == "/api/projects":
                    return self._send(200, store.create_project(
                        body["name"], spec_text=body.get("spec_text"),
                        pack=body.get("pack"), policy_ids=body.get("policy_ids"),
                    ))
                if self.path == "/api/policies":
                    return self._send(200, store.add_policy(body["name"], body["constraints"]))
                m = re.match(r"^/api/projects/([^/]+)/(simulate|autofix)$", self.path)
                if m:
                    action = getattr(store, "simulate" if m.group(2) == "simulate" else "autofix")
                    return self._send(200, action(m.group(1)))
                m = re.match(r"^/api/projects/([^/]+)/run$", self.path)
                if m:
                    return self._send(200, store.run_project(
                        m.group(1), body["message"], body.get("approved", False)))
            except (KeyError, ValueError) as exc:
                return self._send(400, {"error": str(exc)})
            self._send(404, {"error": "not found"})

        def do_DELETE(self):
            m = re.match(r"^/api/projects/([^/]+)$", self.path)
            if m:
                store.delete_project(m.group(1))
                return self._send(200, {"deleted": m.group(1)})
            self._send(404, {"error": "not found"})

    return Handler


def serve(data_dir: str = ".agentproof-server", port: int = 4600, open_browser: bool = True) -> None:
    store = ProjectStore(data_dir)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store))
    url = f"http://127.0.0.1:{port}"
    print(f"AgentProof team backend running at {url}  (data: {data_dir}, Ctrl-C to stop)")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBackend stopped.")
