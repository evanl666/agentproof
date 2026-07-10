"""Counterexample replay movie — make a proof violation impossible to miss.

`agentproof prove` returns a counterexample path as text. This renders it as a
self-contained HTML "movie": the graph on a canvas, and for each violated
property, the bypassing path animates in red step by step — input flowing
straight to the money tool with no gate in the way, or PII reaching an external
channel unredacted. A security hole nobody can un-see; perfect for a README, an
issue, or a PR comment.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentproof.graph import AgentGraph
from agentproof.proofs import prove
from agentproof.report import CANVAS_CSS, CANVAS_JS
from agentproof.spec import BehaviorSpec

_MOVIE_JS = """
const DATA = __DATA__;
const svg = document.getElementById('graph');
renderGraph(svg, DATA.graph, () => {});
let timer = null;

function resetEdges() {
  svg.querySelectorAll('path[data-edge]').forEach(p => {
    p.setAttribute('stroke', '#30363d'); p.setAttribute('stroke-width', '1.5');
    p.setAttribute('marker-end', 'url(#arrow)');
  });
  svg.querySelectorAll('.node rect').forEach(r => r.setAttribute('fill', '#161b22'));
}

function playPath(path, color, marker) {
  clearInterval(timer);
  resetEdges();
  let i = 0;
  function step() {
    if (i < path.length) {
      const r = svg.querySelector(`.node[data-node="${path[i]}"] rect`);
      if (r) r.setAttribute('fill', color === '#f85149' ? 'rgba(248,81,73,.3)' : 'rgba(63,185,80,.12)');
    }
    if (i > 0) {
      const p = svg.querySelector(`path[data-edge="${path[i-1]}->${path[i]}"]`);
      if (p) { p.setAttribute('stroke', color); p.setAttribute('stroke-width', '3'); p.setAttribute('marker-end', marker); }
    }
    i++;
    if (i > path.length) clearInterval(timer);
  }
  timer = setInterval(step, 550);
  step();
}

function selectProof(idx, el) {
  document.querySelectorAll('.proofitem').forEach(x => x.classList.remove('active'));
  el.classList.add('active');
  const p = DATA.proofs[idx];
  const d = document.getElementById('detail');
  if (p.holds) {
    d.innerHTML = `<h3>✓ ${p.property}</h3><p class="note">${p.detail}</p>` +
      `<p class="note">This property is proven — no bypassing path exists.</p>`;
    resetEdges();
    return;
  }
  d.innerHTML = `<h3>✗ ${p.property}</h3><div class="violation">${p.detail}</div>` +
    `<p class="note">Counterexample path (animating in red):</p>` +
    `<p class="note">${p.counterexample.join(' → ')}</p>`;
  playPath(p.counterexample, '#f85149', 'url(#arrow-red)');
}

const list = document.getElementById('proofs');
DATA.proofs.forEach((p, i) => {
  const div = document.createElement('div');
  div.className = 'proofitem ' + (p.holds ? 'pass' : 'fail');
  div.innerHTML = `<span class="status">${p.holds ? 'PROVEN' : 'VIOLATED'}</span><div>${p.property}</div>`;
  div.addEventListener('click', () => selectProof(i, div));
  list.appendChild(div);
});
const firstFail = DATA.proofs.findIndex(p => !p.holds);
const idx = firstFail >= 0 ? firstFail : 0;
if (DATA.proofs.length) selectProof(idx, list.children[idx]);
"""


def build_proof_movie_html(graph: AgentGraph, spec: BehaviorSpec) -> str:
    proofs = prove(graph, spec)
    data = json.dumps({
        "graph": graph.to_dict(),
        "proofs": [p.to_dict() for p in proofs],
    }).replace("</", "<\\/")
    holding = sum(1 for p in proofs if p.holds)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>AgentProof · counterexample replay</title>
<style>{CANVAS_CSS}
.proofitem {{ padding: 10px 12px; border-bottom: 1px solid var(--border); cursor: pointer; }}
.proofitem:hover, .proofitem.active {{ background: #1f2733; }}
.proofitem .status {{ float: right; font-weight: 700; font-size: 11px; }}
.proofitem.pass .status {{ color: var(--green); }} .proofitem.fail .status {{ color: var(--red); }}
</style></head>
<body>
<header><h1>⚡ AgentProof · Counterexample replay</h1>
  <span class="chip {'good' if holding == len(proofs) else 'bad'}"><b>{holding}/{len(proofs)}</b> properties proven</span>
</header>
<div class="layout">
  <div class="panel"><h2>Safety properties</h2><div id="proofs"></div></div>
  <div class="panel" id="canvas-wrap"><h2>Agent graph</h2><svg id="graph"></svg></div>
  <div class="panel"><h2>Details</h2><div class="detail" id="detail"></div></div>
</div>
<script>{CANVAS_JS}</script>
<script>{_MOVIE_JS.replace("__DATA__", data)}</script>
</body></html>"""


def write_proof_movie(path: str | Path, graph: AgentGraph, spec: BehaviorSpec) -> Path:
    path = Path(path)
    path.write_text(build_proof_movie_html(graph, spec))
    return path
