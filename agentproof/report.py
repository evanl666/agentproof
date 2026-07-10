"""Canvas Replay: a self-contained HTML report of a simulation run.

Every scenario replays on the graph: click one and its path lights up —
green for passing runs, red for the exact edges where a violation happened.
No server, no dependencies; the file works from disk or in CI artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentproof.coverage import CoverageReport
from agentproof.graph import AgentGraph
from agentproof.score import AgentScore
from agentproof.simulator import SimulationResult
from agentproof.spec import BehaviorSpec

# Shared by the static report and the live Studio app.
CANVAS_CSS = """
:root {
  --bg: #0d1117; --panel: #161b22; --border: #30363d; --text: #e6edf3;
  --muted: #8b949e; --green: #3fb950; --red: #f85149; --amber: #d29922;
  --blue: #58a6ff; --purple: #bc8cff;
}
* { box-sizing: border-box; margin: 0; }
body { background: var(--bg); color: var(--text); font: 14px/1.5 -apple-system, 'Segoe UI', sans-serif; }
.layout { display: grid; grid-template-columns: 300px 1fr 320px; gap: 12px; padding: 12px; height: calc(100vh - 64px); }
.panel { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; overflow-y: auto; }
.panel h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); padding: 10px 12px; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--panel); }
header { display: flex; align-items: center; gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
header h1 { font-size: 16px; }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 2px 10px; border-radius: 999px; font-size: 12px; border: 1px solid var(--border); }
.chip b { font-size: 13px; }
.chip.good b { color: var(--green); } .chip.bad b { color: var(--red); } .chip.warn b { color: var(--amber); }
.scenario { padding: 8px 12px; border-bottom: 1px solid var(--border); cursor: pointer; }
.scenario:hover, .scenario.active { background: #1f2733; }
.scenario .cat { font-size: 11px; color: var(--muted); }
.scenario .status { float: right; font-weight: 700; }
.pass .status { color: var(--green); } .fail .status { color: var(--red); }
#canvas-wrap { overflow: auto; }
svg text { fill: var(--text); font: 11px -apple-system, sans-serif; pointer-events: none; }
.node rect { stroke-width: 1.5; cursor: pointer; }
.detail { padding: 12px; }
.detail h3 { font-size: 13px; margin-bottom: 6px; }
.violation { color: var(--red); font-size: 12px; margin: 6px 0; padding: 6px 8px; background: rgba(248,81,73,.1); border-left: 3px solid var(--red); border-radius: 4px; }
.note { color: var(--muted); font-size: 12px; margin: 4px 0; }
.covbar { height: 8px; background: var(--border); border-radius: 4px; margin: 8px 12px; overflow: hidden; }
.covbar div { height: 100%; background: var(--blue); }
.fixitem { font-size: 12px; margin: 6px 0; padding: 6px 8px; background: rgba(63,185,80,.08); border-left: 3px solid var(--green); border-radius: 4px; }
.muted { color: var(--muted); }
"""

CANVAS_JS = """
const NODE_COLORS = {
  input: '#58a6ff', llm: '#bc8cff', tool: '#d29922', condition: '#79c0ff',
  approval: '#ff9bce', guard: '#3fb950', fallback: '#f0883e', output: '#8b949e'
};

function layoutGraph(graph) {
  const depth = {}; const incoming = {};
  graph.nodes.forEach(n => { incoming[n.id] = 0; });
  graph.edges.forEach(e => { incoming[e.target] = (incoming[e.target] || 0) + 1; });
  const roots = graph.nodes.filter(n => n.type === 'input' || incoming[n.id] === 0);
  const queue = roots.map(n => n.id);
  roots.forEach(n => depth[n.id] = 0);
  const seen = new Set(queue);
  while (queue.length) {
    const id = queue.shift();
    graph.edges.filter(e => e.source === id).forEach(e => {
      if (!seen.has(e.target)) {
        depth[e.target] = (depth[id] || 0) + 1;
        seen.add(e.target); queue.push(e.target);
      }
    });
  }
  graph.nodes.forEach(n => { if (depth[n.id] === undefined) depth[n.id] = 1; });
  const cols = {};
  graph.nodes.forEach(n => { (cols[depth[n.id]] = cols[depth[n.id]] || []).push(n); });
  const pos = {}; const W = 168, H = 48, GX = 250, GY = 92;
  Object.keys(cols).sort((a, b) => a - b).forEach(d => {
    cols[d].forEach((n, i) => {
      pos[n.id] = { x: 30 + d * GX, y: 30 + i * GY + (d % 2) * 16, w: W, h: H };
    });
  });
  return pos;
}

function edgePath(a, b, bend) {
  bend = bend || 0;
  const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
  if (x2 <= x1) {
    // Back/return edge: bow downward, below the nodes, fanned by `bend` so the
    // many tool->planner returns don't pile into one thick bundle.
    const sx = a.x + a.w / 2, tx = b.x + b.w / 2;
    const midY = Math.max(a.y + a.h, b.y + b.h) + 34 + bend;
    return `M ${sx} ${a.y + a.h} C ${sx} ${midY}, ${tx} ${midY}, ${tx} ${b.y + b.h}`;
  }
  const mx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
}

// Session-scoped position overrides so dragged nodes stay put across re-renders.
window.NODE_POS = window.NODE_POS || {};
// Shared drag state + global listeners installed exactly once (no per-render leak).
window.__drag = window.__drag || null;
if (!window.__dragBound) {
  window.__dragBound = true;
  window.addEventListener('mousemove', evt => {
    const d = window.__drag; if (!d) return;
    const pt = d.svgPoint(evt);
    d.pos[d.id].x = Math.max(0, pt.x - d.off.x);
    d.pos[d.id].y = Math.max(0, pt.y - d.off.y);
    window.NODE_POS[d.id] = { x: d.pos[d.id].x, y: d.pos[d.id].y };
    d.moved = true; d.reposition(d.id);
  });
  window.addEventListener('mouseup', () => {
    if (window.__drag) {
      window.__lastDragMoved = window.__drag.moved;
      if (window.__drag.g) window.__drag.g.style.cursor = 'grab';
      window.__drag = null;
    }
  });
}
function renderGraph(svg, graph, onNodeClick) {
  const pos = layoutGraph(graph);
  // Apply any user drags from this session.
  Object.keys(pos).forEach(id => {
    if (window.NODE_POS[id]) { pos[id].x = window.NODE_POS[id].x; pos[id].y = window.NODE_POS[id].y; }
  });
  const maxX = Math.max(...Object.values(pos).map(p => p.x + p.w)) + 60;
  const maxY = Math.max(...Object.values(pos).map(p => p.y + p.h)) + 90;
  svg.setAttribute('width', maxX); svg.setAttribute('height', maxY);
  svg.innerHTML = `<defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#8b949e"/></marker>
    <marker id="arrow-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#f85149"/></marker>
    <marker id="arrow-green" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#3fb950"/></marker>
  </defs>`;
  const ns = 'http://www.w3.org/2000/svg';
  // "tool call" / "result" repeat on every agent-loop edge — pure noise; hide them.
  const NOISE_LABELS = { 'tool call': 1, 'result': 1, '': 1 };
  const backSeen = {};  // fan parallel return edges apart per target
  graph.edges.forEach((e) => {
    const a = pos[e.source], b = pos[e.target];
    if (!a || !b) return;
    const isNoise = !!NOISE_LABELS[e.label];
    const isBack = b.x <= a.x;
    // Only the unlabeled agent-loop returns recede; a labelled back-edge
    // (rejected / on error) stays solid so its meaning is visible.
    const fade = isBack && isNoise;
    let bend = 0;
    if (fade) { bend = (backSeen[e.target] || 0) * 16; backSeen[e.target] = (backSeen[e.target] || 0) + 1; }
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', edgePath(a, b, bend));
    path.setAttribute('fill', 'none');
    path.dataset.edge = e.source + '->' + e.target;
    path.dataset.bend = bend;
    if (fade) {
      path.setAttribute('stroke', '#3a4150');
      path.setAttribute('stroke-width', '1');
      path.setAttribute('stroke-opacity', '0.35');
      path.setAttribute('stroke-dasharray', '3 4');
      path.dataset.back = '1';
    } else {
      path.setAttribute('stroke', '#4a5160');
      path.setAttribute('stroke-width', '1.5');
      path.setAttribute('marker-end', 'url(#arrow)');
    }
    svg.appendChild(path);
    if (!isNoise) {  // draw meaningful labels in any direction
      const t = document.createElementNS(ns, 'text');
      const x1 = a.x + a.w, x2 = b.x;
      t.setAttribute('x', (x1 + x2) / 2 - 20);
      t.setAttribute('y', (a.y + b.y + a.h) / 2 - 6);
      t.textContent = e.label; t.setAttribute('opacity', '0.65');
      svg.appendChild(t);
    }
  });
  // Update the SVG geometry of a node + the edges touching it (used while dragging).
  function reposition(id) {
    const p = pos[id];
    const g = svg.querySelector('.node[data-node="' + id + '"]');
    if (!g) return;
    g.querySelector('rect').setAttribute('x', p.x);
    g.querySelector('rect').setAttribute('y', p.y);
    const ts = g.querySelectorAll('text');
    ts[0].setAttribute('x', p.x + 10); ts[0].setAttribute('y', p.y + 19);
    ts[1].setAttribute('x', p.x + 10); ts[1].setAttribute('y', p.y + 35);
    svg.querySelectorAll('path[data-edge]').forEach(path => {
      const [s, t] = path.dataset.edge.split('->');
      if (s === id || t === id) path.setAttribute('d', edgePath(pos[s], pos[t], +path.dataset.bend || 0));
    });
  }
  function svgPoint(evt) {
    const r = svg.getBoundingClientRect();
    const vb = svg.viewBox.baseVal;
    const sx = vb && vb.width ? vb.width / r.width : 1;
    const sy = vb && vb.height ? vb.height / r.height : 1;
    return { x: (evt.clientX - r.left) * sx, y: (evt.clientY - r.top) * sy };
  }
  graph.nodes.forEach(n => {
    const p = pos[n.id];
    const g = document.createElementNS(ns, 'g');
    g.setAttribute('class', 'node'); g.dataset.node = n.id;
    g.style.cursor = 'grab';
    const rect = document.createElementNS(ns, 'rect');
    rect.setAttribute('x', p.x); rect.setAttribute('y', p.y);
    rect.setAttribute('width', p.w); rect.setAttribute('height', p.h);
    rect.setAttribute('rx', 8); rect.setAttribute('fill', '#161b22');
    rect.setAttribute('stroke', NODE_COLORS[n.type] || '#8b949e');
    g.appendChild(rect);
    const t1 = document.createElementNS(ns, 'text');
    t1.setAttribute('x', p.x + 10); t1.setAttribute('y', p.y + 19);
    t1.setAttribute('font-weight', '600');
    t1.textContent = n.label.length > 20 ? n.label.slice(0, 19) + '…' : n.label;
    const t2 = document.createElementNS(ns, 'text');
    t2.setAttribute('x', p.x + 10); t2.setAttribute('y', p.y + 35);
    t2.setAttribute('opacity', '0.6'); t2.textContent = n.type;
    g.appendChild(t1); g.appendChild(t2);
    // Drag to rearrange; a click that doesn't move still fires onNodeClick.
    g.addEventListener('mousedown', evt => {
      g.style.cursor = 'grabbing';
      const pt = svgPoint(evt);
      window.__drag = {
        id: n.id, g, pos, reposition, svgPoint, moved: false,
        off: { x: pt.x - pos[n.id].x, y: pt.y - pos[n.id].y },
      };
      evt.preventDefault();
    });
    g.addEventListener('click', () => {
      const wasDrag = window.__lastDragMoved; window.__lastDragMoved = false;
      if (!wasDrag && onNodeClick) onNodeClick(n);
    });
    svg.appendChild(g);
  });
}

function resetEdges(svg) {
  svg.querySelectorAll('path[data-edge]').forEach(p => {
    if (p.dataset.back) {  // keep faded return edges receded
      p.setAttribute('stroke', '#3a4150'); p.setAttribute('stroke-width', '1');
      p.setAttribute('stroke-opacity', '0.35'); p.removeAttribute('marker-end');
    } else {
      p.setAttribute('stroke', '#4a5160'); p.setAttribute('stroke-width', '1.5');
      p.setAttribute('stroke-opacity', '1'); p.setAttribute('marker-end', 'url(#arrow)');
    }
  });
}
function highlightResult(svg, result) {
  resetEdges(svg);
  svg.querySelectorAll('.node rect').forEach(r => r.setAttribute('fill', '#161b22'));
  if (!result) return;
  const color = result.passed ? '#3fb950' : '#f85149';
  const marker = result.passed ? 'url(#arrow-green)' : 'url(#arrow-red)';
  result.visited_edges.forEach(([a, b]) => {
    const p = svg.querySelector(`path[data-edge="${a}->${b}"]`);
    if (p) {
      p.setAttribute('stroke', color);
      p.setAttribute('stroke-width', '2.5');
      p.setAttribute('marker-end', marker);
    }
  });
  const violated = new Set((result.violations || []).map(v => v.node_id).filter(Boolean));
  result.visited_nodes.forEach(id => {
    const r = svg.querySelector(`.node[data-node="${id}"] rect`);
    if (r) r.setAttribute('fill', violated.has(id) ? 'rgba(248,81,73,.25)' : 'rgba(63,185,80,.08)');
  });
}

// Policy visualizer: draw the contract's policy lines directly on the canvas —
// green (dashed) when a guard/gate satisfies the constraint, red when it's open.
function drawPolicyLines(svg, graph, policyLines) {
  svg.querySelectorAll('.policy-line').forEach(el => el.remove());
  if (!policyLines || !policyLines.length) return;
  const pos = layoutGraph(graph);
  const ns = 'http://www.w3.org/2000/svg';
  policyLines.forEach(line => {
    const a = pos[line.source], b = pos[line.target];
    if (!a || !b) return;
    const color = line.satisfied ? '#3fb950' : '#f85149';
    const path = document.createElementNS(ns, 'path');
    // Arc above the nodes so the policy line reads distinctly from data edges.
    const x1 = a.x + a.w / 2, x2 = b.x + b.w / 2;
    const topY = Math.min(a.y, b.y) - 26;
    path.setAttribute('d', `M ${x1} ${a.y} C ${x1} ${topY}, ${x2} ${topY}, ${x2} ${b.y}`);
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', color);
    path.setAttribute('stroke-width', line.satisfied ? '2' : '3');
    path.setAttribute('stroke-dasharray', '6 4');
    path.setAttribute('opacity', line.satisfied ? '0.55' : '0.9');
    path.setAttribute('class', 'policy-line');
    const title = document.createElementNS(ns, 'title');
    title.textContent = (line.satisfied ? '✓ ' : '✗ OPEN — ') + line.label;
    path.appendChild(title);
    svg.appendChild(path);
  });
}
"""

_REPORT_JS = """
const DATA = __DATA__;
const svg = document.getElementById('graph');
renderGraph(svg, DATA.graph, node => showNodeDetail(node));

const list = document.getElementById('scenarios');
DATA.results.forEach((r, i) => {
  const div = document.createElement('div');
  div.className = 'scenario ' + (r.passed ? 'pass' : 'fail');
  div.innerHTML = `<span class="status">${r.passed ? 'PASS' : 'FAIL'}</span>` +
    `<div>${r.scenario.id}</div><div class="cat">${r.scenario.description}</div>`;
  div.addEventListener('click', () => select(i, div));
  list.appendChild(div);
});

function select(i, el) {
  document.querySelectorAll('.scenario').forEach(s => s.classList.remove('active'));
  el.classList.add('active');
  const r = DATA.results[i];
  highlightResult(svg, r);
  const d = document.getElementById('detail');
  let html = `<h3>${r.scenario.id}</h3><p class="note">"${r.scenario.user_message}"</p>`;
  (r.violations || []).forEach(v => { html += `<div class="violation"><b>${v.kind}</b><br>${v.message}</div>`; });
  (r.notes || []).forEach(n => { html += `<div class="note">• ${n}</div>`; });
  html += `<div class="note" style="margin-top:8px">Cost: ${r.cost_tokens.toLocaleString()} tokens` +
    (r.approval_requested ? ' · human approval requested' : '') + '</div>';
  d.innerHTML = html;
}

function showNodeDetail(node) {
  const d = document.getElementById('detail');
  d.innerHTML = `<h3>${node.label}</h3><p class="note">type: ${node.type}</p>` +
    `<pre class="note" style="white-space:pre-wrap">${JSON.stringify(node.config, null, 2)}</pre>`;
}

let policyShown = false;
const policyToggle = document.getElementById('policy-toggle');
if (policyToggle) policyToggle.addEventListener('click', () => {
  policyShown = !policyShown;
  drawPolicyLines(svg, DATA.graph, policyShown ? DATA.policy.lines : []);
  const d = document.getElementById('detail');
  if (policyShown && DATA.policy) {
    d.innerHTML = '<h3>Policy lines</h3>' + DATA.policy.lines.map(l =>
      `<div class="${l.satisfied ? 'note' : 'violation'}">` +
      `${l.satisfied ? '✓' : '✗ OPEN'} ${l.source} ⇒ ${l.target}<br>${l.label}</div>`
    ).join('');
  }
});

const firstFail = DATA.results.findIndex(r => !r.passed);
const idx = firstFail >= 0 ? firstFail : 0;
if (DATA.results.length) select(idx, list.children[idx]);
"""


def build_report_html(
    spec: BehaviorSpec,
    graph: AgentGraph,
    results: list[SimulationResult],
    coverage: CoverageReport,
    score: AgentScore,
    fixes: list | None = None,
) -> str:
    from agentproof.policy_lines import policy_summary

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    total_cost = sum(r.cost_usd for r in results)
    policy = policy_summary(graph, spec)
    data = {
        "spec": spec.to_dict(),
        "graph": graph.to_dict(),
        "results": [r.to_dict() for r in results],
        "coverage": coverage.to_dict(),
        "score": score.to_dict(),
        "policy": policy,
    }
    report_js = _REPORT_JS.replace(
        "__DATA__", json.dumps(data).replace("</", "<\\/")
    )
    fixes_html = ""
    if fixes:
        items = "".join(f'<div class="fixitem">{f.description}</div>' for f in fixes)
        fixes_html = f'<h2>Auto-fixes applied</h2><div class="detail">{items}</div>'
    verdict_class = "good" if score.shippable else "bad"
    verdict = "SHIPPABLE" if score.shippable else "NOT SHIPPABLE"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>AgentProof · {spec.name}</title>
<style>{CANVAS_CSS}</style></head>
<body>
<header>
  <h1>AgentProof · {spec.name}</h1>
  <span class="chip {'good' if passed == total else 'bad'}"><b>{passed}/{total}</b> tests</span>
  <span class="chip good"><b>{score.safety}</b> safety</span>
  <span class="chip"><b>{score.reliability}</b> reliability</span>
  <span class="chip"><b>{round(coverage.overall * 100)}%</b> coverage</span>
  <span class="chip"><b>${total_cost:.2f}</b> / {total} requests</span>
  <span class="chip {verdict_class}"><b>{verdict}</b> · score {score.overall}</span>
  <span class="chip {'good' if policy['open'] == 0 else 'bad'}" id="policy-toggle" style="cursor:pointer">
    <b>{policy['satisfied']}/{policy['total']}</b> policy lines
  </span>
</header>
<div class="layout">
  <div class="panel"><h2>Simulation arena ({total} scenarios)</h2><div id="scenarios"></div></div>
  <div class="panel" id="canvas-wrap"><h2>Canvas replay</h2><svg id="graph"></svg></div>
  <div class="panel"><h2>Details</h2><div class="detail" id="detail">
    <p class="muted">Select a scenario to replay it on the canvas.</p></div>
    {fixes_html}
  </div>
</div>
<script>{CANVAS_JS}</script>
<script>{report_js}</script>
</body></html>"""


def write_report(
    path: str | Path,
    spec: BehaviorSpec,
    graph: AgentGraph,
    results: list[SimulationResult],
    coverage: CoverageReport,
    score: AgentScore,
    fixes: list | None = None,
) -> Path:
    path = Path(path)
    path.write_text(
        build_report_html(spec, graph, results, coverage, score, fixes=fixes)
    )
    return path
