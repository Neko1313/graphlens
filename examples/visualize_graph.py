"""Interactive HTML graph visualisation for graphlens.

Analyses a project (or monorepo) with all applicable language adapters and
opens a self-contained HTML file in the browser.  Works with Python,
TypeScript, or mixed projects — adapters are auto-detected.

Usage:
    uv run python examples/visualize_graph.py <project_root> [options]

Arguments:
    project_root    Path to the project or monorepo root to analyse.

Options:
    --lang LANG         Which adapter(s) to use.  Accepts:
                          auto              detect all installed adapters that
                                            can handle root (default)
                          python            Python adapter only
                          typescript        TypeScript adapter only
                          python,typescript both adapters explicitly
    --output PATH       Write HTML to PATH instead of graph-<name>.html
    --no-open           Do not open the browser automatically
    --show-external     Include stdlib / third-party external symbol nodes
    --show-structure    Add CONTAINS / DECLARES structural edges
    --max-nodes N       Prune low-degree nodes above this count (default: 1500)

Examples:
    # auto-detect language, open in browser
    uv run python examples/visualize_graph.py .

    # mixed Python + TypeScript monorepo
    uv run python examples/visualize_graph.py ~/myrepo --lang python,typescript

    # limit graph size, save to a specific file
    uv run python examples/visualize_graph.py ~/myproject --max-nodes 500 --output out.html

    # include external stdlib/third-party symbols
    uv run python examples/visualize_graph.py . --show-external --show-structure

Click behaviour in the browser:
    Click any node              → info panel (qualified name, kind, metadata)
    Click FUNCTION / METHOD     → info panel with "Show callers" button
    "Show callers"              → focus mode: the selected node + every node
                                  that calls or references it, callers listed
                                  in the sidebar
    Click empty space / ← Back → exit focus mode, restore full graph
    Search box                  → filter visible nodes by label (exits focus)
"""

from __future__ import annotations

import argparse
import json
import time
import webbrowser
from collections import defaultdict
from pathlib import Path

from graphlens import GraphLens, NodeKind, RelationKind

# ── colour palettes ───────────────────────────────────────────────────────────

_NODE_COLOR: dict[str, str] = {
    "project":         "#FFD700",
    "module":          "#4A90D9",
    "file":            "#7BB3E0",
    "class":           "#5CB85C",
    "function":        "#F0A830",
    "method":          "#F5C842",
    "parameter":       "#AAAAAA",
    "import":          "#9B59B6",
    "external_symbol": "#888888",
    "dependency":      "#FF6B9D",
    "variable":        "#7FC97F",
    "attribute":       "#A8D8A8",
    "type_alias":      "#DDA0DD",
}

_ORIGIN_COLOR: dict[str, str] = {
    "stdlib":       "#606060",
    "third_party":  "#994444",
    "internal":     "#3A6080",
    "unknown":      "#555555",
}

_EDGE_COLOR: dict[str, str] = {
    "contains":      "#444466",
    "declares":      "#444466",
    "calls":         "#E74C3C",
    "references":    "#E67E22",
    "inherits_from": "#8E44AD",
    "has_type":      "#2980B9",
    "imports":       "#27AE60",
    "resolves_to":   "#1A7A40",
    "depends_on":    "#FF6B9D",
}

_STRUCTURAL = {RelationKind.CONTAINS, RelationKind.DECLARES}

_NODE_SHAPE: dict[NodeKind, str] = {
    NodeKind.PROJECT:         "star",
    NodeKind.MODULE:          "box",
    NodeKind.FILE:            "box",
    NodeKind.CLASS:           "ellipse",
    NodeKind.FUNCTION:        "dot",
    NodeKind.METHOD:          "dot",
    NodeKind.PARAMETER:       "triangle",
    NodeKind.IMPORT:          "diamond",
    NodeKind.EXTERNAL_SYMBOL: "square",
    NodeKind.DEPENDENCY:      "hexagon",
    NodeKind.VARIABLE:        "triangleDown",
    NodeKind.ATTRIBUTE:       "triangleDown",
    NodeKind.TYPE_ALIAS:      "diamond",
}

# ── multi-language analysis ───────────────────────────────────────────────────

def _resolve_langs(lang_arg: str, root: Path) -> list[str]:
    """Return the list of language names to run based on --lang value."""
    if lang_arg != "auto":
        return [s.strip() for s in lang_arg.split(",") if s.strip()]

    from graphlens import adapter_registry
    available = adapter_registry.available()
    if not available:
        raise SystemExit("No graphlens adapters installed. Install graphlens-python or graphlens-typescript.")

    matched: list[str] = []
    for lang in available:
        try:
            cls = adapter_registry.load(lang)
            if cls().can_handle(root):
                matched.append(lang)
        except Exception:
            pass

    if not matched:
        raise SystemExit(
            f"No adapter can handle {root}\n"
            f"Available: {available}\n"
            "Use --lang to specify explicitly."
        )
    return matched


def _load_adapter(lang: str):
    from graphlens import adapter_registry
    try:
        return adapter_registry.load(lang)()
    except Exception:
        # fallback for adapters not yet registered via entry points
        if lang == "python":
            from graphlens_python import PythonAdapter
            return PythonAdapter()
        if lang == "typescript":
            from graphlens_typescript import TypescriptAdapter
            return TypescriptAdapter()
        raise SystemExit(f"Unknown or unavailable adapter: {lang!r}")


def _merge_graph(target: GraphLens, source: GraphLens) -> None:
    """Merge source into target in-place, skipping duplicate node IDs."""
    for nid, node in source.nodes.items():
        if nid not in target.nodes:
            target.add_node(node)
    for rel in source.relations:
        target.add_relation(rel)


def _run_analysis(root: Path, langs: list[str]) -> tuple[GraphLens, float]:
    combined = GraphLens()
    t0 = time.time()
    for lang in langs:
        print(f"[graphlens] adapter={lang}  root={root}")
        adapter = _load_adapter(lang)
        g = adapter.analyze(root)
        print(
            f"[graphlens]   {lang}: nodes={len(g.nodes)}  relations={len(g.relations)}"
        )
        _merge_graph(combined, g)
    elapsed = round(time.time() - t0, 2)
    return combined, elapsed


# ── graph → vis.js data ───────────────────────────────────────────────────────

def _build_vis_data(
    graph: GraphLens,
    *,
    show_external: bool,
    show_structure: bool,
    max_nodes: int,
) -> tuple[list[dict], list[dict]]:
    nodes_map = graph.nodes

    degree: dict[str, int] = defaultdict(int)
    for r in graph.relations:
        degree[r.source_id] += 1
        degree[r.target_id] += 1

    candidate_ids: set[str] = set()
    for nid, node in nodes_map.items():
        if node.kind == NodeKind.EXTERNAL_SYMBOL and not show_external:
            continue
        candidate_ids.add(nid)

    if len(candidate_ids) > max_nodes:
        pinned = {
            nid for nid in candidate_ids
            if nodes_map[nid].kind in (NodeKind.PROJECT, NodeKind.MODULE, NodeKind.FILE)
        }
        others = sorted(
            candidate_ids - pinned,
            key=lambda nid: degree[nid],
            reverse=True,
        )
        cap = max(0, max_nodes - len(pinned))
        candidate_ids = pinned | set(others[:cap])

    vis_nodes: list[dict] = []
    for nid in candidate_ids:
        node = nodes_map[nid]
        kind = node.kind.value

        if node.kind == NodeKind.EXTERNAL_SYMBOL:
            origin = str(node.metadata.get("origin", "unknown"))
            bg = _ORIGIN_COLOR.get(origin, "#555555")
        else:
            bg = _NODE_COLOR.get(kind, "#888888")

        meta_skip = {"name_span"}
        meta_lines = "".join(
            f"<br/><span style='color:#aaa'>{k}:</span> {v}"
            for k, v in node.metadata.items()
            if k not in meta_skip
        )
        is_callable = node.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
        callers_btn = (
            "<br/><br/>"
            "<button onclick=\"showCallers('"
            + nid
            + "',this)\" "
            "style='background:#1f2937;border:1px solid #e94560;color:#ff7b7b;"
            "padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px'>"
            "Show callers</button>"
        ) if is_callable else ""

        tooltip = (
            f"<div style='font-family:monospace;max-width:320px'>"
            f"<b style='color:#FFD700'>{node.qualified_name}</b>"
            f"<br/><i style='color:#aaa'>{kind}</i>"
            f"{meta_lines}"
            f"{callers_btn}"
            f"</div>"
        )

        label = node.name or node.qualified_name.split(".")[-1] or kind
        vis_nodes.append({
            "id":    nid,
            "label": label,
            "title": tooltip,
            "color": {
                "background":  bg,
                "border":      "#222",
                "highlight":   {"background": bg, "border": "#fff"},
                "hover":       {"background": bg, "border": "#eee"},
            },
            "group": kind,
            "shape": _NODE_SHAPE.get(node.kind, "dot"),
            "font":  {"size": 12, "color": "#e0e0e0"},
        })

    vis_edges: list[dict] = []
    for idx, rel in enumerate(graph.relations):
        if rel.source_id not in candidate_ids or rel.target_id not in candidate_ids:
            continue
        if rel.kind in _STRUCTURAL and not show_structure:
            continue
        kind = rel.kind.value
        color = _EDGE_COLOR.get(kind, "#666666")
        is_structural = rel.kind in _STRUCTURAL
        vis_edges.append({
            "id":     idx,
            "from":   rel.source_id,
            "to":     rel.target_id,
            "_kind":  kind,
            "title":  kind,
            "color":  {"color": color, "highlight": color, "hover": "#ffffff", "opacity": 0.75},
            "arrows": "to",
            "width":  1 if is_structural else 2,
            "dashes": is_structural,
        })

    return vis_nodes, vis_edges


# ── HTML rendering ────────────────────────────────────────────────────────────

def _render_html(
    project_name: str,
    vis_nodes: list[dict],
    vis_edges: list[dict],
    stats: dict,
) -> str:
    nodes_json = json.dumps(vis_nodes, ensure_ascii=False)
    edges_json = json.dumps(vis_edges, ensure_ascii=False)

    node_kinds = sorted({n["group"] for n in vis_nodes})
    edge_kinds = sorted({e["_kind"] for e in vis_edges})

    def node_filter_row(k: str) -> str:
        color = _NODE_COLOR.get(k, "#888")
        return (
            f'<label class="filter-row">'
            f'<input type="checkbox" class="nf" value="{k}" checked>'
            f'<span class="chip" style="background:{color}"></span>'
            f'{k}</label>'
        )

    def edge_filter_row(k: str) -> str:
        color = _EDGE_COLOR.get(k, "#666")
        return (
            f'<label class="filter-row">'
            f'<input type="checkbox" class="ef" value="{k}" checked>'
            f'<span class="chip" style="background:{color}"></span>'
            f'{k}</label>'
        )

    node_filters = "\n".join(node_filter_row(k) for k in node_kinds)
    edge_filters = "\n".join(edge_filter_row(k) for k in edge_kinds)
    is_pruned = len(vis_nodes) < stats.get("total_nodes", 0)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>graphlens — {project_name}</title>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ display: flex; height: 100vh; font-family: -apple-system,system-ui,sans-serif;
       background: #0d1117; color: #c9d1d9; overflow: hidden; }}

#sidebar {{
  width: 220px; min-width: 220px; background: #161b22;
  border-right: 1px solid #30363d; display: flex; flex-direction: column;
  overflow: hidden;
}}
#sidebar-header {{
  padding: 12px; border-bottom: 1px solid #30363d; flex-shrink: 0;
}}
#sidebar-header h2 {{ font-size: 11px; text-transform: uppercase; letter-spacing: 1px;
                      color: #e94560; margin-bottom: 4px; }}
#project-name {{ font-size: 13px; font-weight: 600; color: #FFD700;
                 word-break: break-all; line-height: 1.3; }}
#sidebar-body {{ flex: 1; overflow-y: auto; padding: 10px; display: flex;
                 flex-direction: column; gap: 12px; }}

.stats-grid {{ display: grid; grid-template-columns: 1fr auto; gap: 2px 8px; font-size: 11px; }}
.stats-grid .k {{ color: #8b949e; }}
.stats-grid .v {{ color: #e6edf3; font-weight: 600; text-align: right; }}

.section-title {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.8px;
                  color: #8b949e; margin-bottom: 4px; }}
.filter-section {{ display: flex; flex-direction: column; gap: 1px; }}
.filter-row {{ display: flex; align-items: center; gap: 6px; font-size: 11px;
               cursor: pointer; padding: 2px 3px; border-radius: 3px; }}
.filter-row:hover {{ background: #21262d; }}
.filter-row input {{ cursor: pointer; accent-color: #58a6ff; }}
.chip {{ width: 8px; height: 8px; border-radius: 2px; flex-shrink: 0; }}

.btn-group {{ display: flex; flex-direction: column; gap: 4px; }}
.btn {{
  background: #21262d; border: 1px solid #30363d; color: #c9d1d9;
  padding: 5px 8px; border-radius: 6px; cursor: pointer; font-size: 11px;
  text-align: left; transition: background 0.15s;
}}
.btn:hover {{ background: #30363d; }}
.btn.primary {{ border-color: #e94560; color: #ff7b7b; }}
.btn.primary:hover {{ background: #e94560; color: #fff; }}

#search {{ width: 100%; background: #0d1117; border: 1px solid #30363d; color: #c9d1d9;
           padding: 5px 8px; border-radius: 6px; font-size: 11px; }}
#search::placeholder {{ color: #484f58; }}
#search:focus {{ outline: none; border-color: #58a6ff; }}

/* callers panel — shown instead of filters in focus mode */
#callers-panel {{ display: none; flex-direction: column; gap: 4px; }}
#callers-list {{ display: flex; flex-direction: column; gap: 1px; max-height: 300px; overflow-y: auto; }}
.caller-row {{
  font-size: 11px; padding: 3px 6px; border-radius: 3px; cursor: pointer;
  color: #c9d1d9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.caller-row:hover {{ background: #21262d; }}
.caller-row .caller-kind {{ color: #E74C3C; font-size: 10px; margin-right: 4px; }}

#graph-wrap {{ flex: 1; display: flex; flex-direction: column; position: relative; }}

/* focus mode banner */
#focus-bar {{
  display: none; align-items: center; gap: 10px;
  padding: 7px 14px; background: #1c2128; border-bottom: 1px solid #e94560;
  flex-shrink: 0;
}}
#focus-bar .back-btn {{
  background: none; border: 1px solid #e94560; color: #ff7b7b;
  padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: 11px;
}}
#focus-bar .back-btn:hover {{ background: #e94560; color: #fff; }}
#focus-label {{ font-size: 12px; color: #c9d1d9; }}
#focus-count {{ font-size: 11px; color: #8b949e; margin-left: auto; }}

#graph {{ flex: 1; position: relative; }}

#info-panel {{
  position: absolute; bottom: 16px; right: 16px;
  background: #161b22; border: 1px solid #30363d; border-radius: 8px;
  padding: 12px 14px; max-width: 380px; font-size: 12px;
  display: none; z-index: 10; box-shadow: 0 4px 20px rgba(0,0,0,0.5);
  max-height: 50vh; overflow-y: auto;
}}
#info-panel b {{ color: #FFD700; }}
#info-close {{ float: right; cursor: pointer; color: #8b949e; font-size: 14px; line-height: 1; }}
#info-close:hover {{ color: #fff; }}

#pruned-warning {{
  position: absolute; top: 10px; right: 10px;
  background: #3d1f00; border: 1px solid #e67e22; border-radius: 6px;
  padding: 6px 10px; font-size: 11px; color: #e67e22; z-index: 10;
  display: {"block" if is_pruned else "none"};
}}
</style>
</head>
<body>

<div id="sidebar">
  <div id="sidebar-header">
    <h2>graphlens</h2>
    <div id="project-name">{project_name}</div>
  </div>
  <div id="sidebar-body">

    <div>
      <div class="section-title">Visible</div>
      <div class="stats-grid">
        <span class="k">Nodes</span><span class="v" id="s-nodes">{len(vis_nodes)}</span>
        <span class="k">Edges</span><span class="v" id="s-edges">{len(vis_edges)}</span>
      </div>
    </div>

    <div>
      <div class="section-title">Total (analysed)</div>
      <div class="stats-grid">
        <span class="k">Nodes</span><span class="v">{stats.get("total_nodes", 0)}</span>
        <span class="k">Edges</span><span class="v">{stats.get("total_relations", 0)}</span>
        <span class="k">Lang</span><span class="v">{stats.get("lang","?")}</span>
        <span class="k">Time</span><span class="v">{stats.get("elapsed", 0)}s</span>
      </div>
    </div>

    <div>
      <div class="section-title">Search</div>
      <input id="search" type="text" placeholder="Filter by label…"/>
    </div>

    <!-- normal filters (hidden in focus mode) -->
    <div id="normal-filters">
      <div>
        <div class="section-title">Node kinds</div>
        <div class="filter-section" id="node-filters">
{node_filters}
        </div>
      </div>

      <div>
        <div class="section-title">Edge kinds</div>
        <div class="filter-section" id="edge-filters">
{edge_filters}
        </div>
      </div>
    </div>

    <!-- callers list (shown in focus mode) -->
    <div id="callers-panel">
      <div class="section-title" id="callers-title">Callers</div>
      <div id="callers-list"></div>
    </div>

    <div>
      <div class="section-title">Controls</div>
      <div class="btn-group">
        <button class="btn primary" onclick="network.fit()">Fit to screen</button>
        <button class="btn" id="physics-btn" onclick="togglePhysics()">Disable physics</button>
        <button class="btn" onclick="selectAll(true)">Check all</button>
        <button class="btn" onclick="selectAll(false)">Uncheck all</button>
      </div>
    </div>

  </div>
</div>

<div id="graph-wrap">
  <div id="focus-bar">
    <button class="back-btn" onclick="exitFocus()">← Back</button>
    <span id="focus-label"></span>
    <span id="focus-count"></span>
  </div>
  <div id="graph">
    <div id="pruned-warning">
      ⚠ Showing {len(vis_nodes)} / {stats.get("total_nodes", 0)} nodes (--max-nodes to adjust)
    </div>
  </div>
</div>

<div id="info-panel">
  <span id="info-close" onclick="closeInfo()">✕</span>
  <div id="info-body"></div>
</div>

<script>
var ALL_NODES = {nodes_json};
var ALL_EDGES = {edges_json};

var nodesDS = new vis.DataSet(ALL_NODES);
var edgesDS = new vis.DataSet(ALL_EDGES);

var options = {{
  nodes: {{
    borderWidth: 1,
    size: 14,
    font: {{ size: 12, color: "#c9d1d9" }},
  }},
  edges: {{
    smooth: {{ type: "dynamic" }},
    font: {{ size: 10, color: "#8b949e", align: "middle" }},
    selectionWidth: 3,
  }},
  physics: {{
    enabled: true,
    stabilization: {{ iterations: 200, fit: true }},
    barnesHut: {{
      gravitationalConstant: -4000,
      springConstant: 0.04,
      springLength: 130,
      damping: 0.09,
    }},
  }},
  interaction: {{
    tooltipDelay: 150,
    hideEdgesOnDrag: true,
    multiselect: true,
    hover: true,
  }},
  layout: {{ improvedLayout: false }},
}};

var container = document.getElementById("graph");
var network = new vis.Network(container, {{ nodes: nodesDS, edges: edgesDS }}, options);
var physicsEnabled = true;
var inFocusMode = false;
var searchVal = "";

// ── node lookup maps ──────────────────────────────────────────────────────────
var nodeById = {{}};
ALL_NODES.forEach(function(n) {{ nodeById[n.id] = n; }});

// ── click ─────────────────────────────────────────────────────────────────────
network.on("click", function(params) {{
  if (params.nodes.length === 1) {{
    var node = nodeById[params.nodes[0]];
    if (node) showInfo(node);
  }} else if (!params.nodes.length && !params.edges.length) {{
    closeInfo();
    if (inFocusMode) exitFocus();
  }}
}});

function showInfo(node) {{
  document.getElementById("info-body").innerHTML = node.title || node.label;
  document.getElementById("info-panel").style.display = "block";
}}

function closeInfo() {{
  document.getElementById("info-panel").style.display = "none";
}}

// ── focus mode: show callers ──────────────────────────────────────────────────
function showCallers(nodeId) {{
  var target = nodeById[nodeId];
  if (!target) return;

  var CALLER_KINDS = new Set(["calls", "references"]);

  // edges pointing TO this node with caller kinds
  var inEdges = ALL_EDGES.filter(function(e) {{
    return e.to === nodeId && CALLER_KINDS.has(e._kind);
  }});
  var callerIds = new Set(inEdges.map(function(e) {{ return e.from; }}));
  callerIds.add(nodeId);

  var focusNodes = ALL_NODES.filter(function(n) {{ return callerIds.has(n.id); }});
  var focusEdges = inEdges;

  nodesDS.clear(); nodesDS.add(focusNodes);
  edgesDS.clear(); edgesDS.add(focusEdges);
  document.getElementById("s-nodes").textContent = focusNodes.length;
  document.getElementById("s-edges").textContent = focusEdges.length;
  network.fit();

  // switch sidebar to callers list
  document.getElementById("normal-filters").style.display = "none";
  document.getElementById("callers-panel").style.display = "flex";
  document.getElementById("callers-title").textContent =
    "Callers of " + target.label;

  var list = document.getElementById("callers-list");
  list.innerHTML = "";
  var callerCount = 0;
  inEdges.forEach(function(e) {{
    var caller = nodeById[e.from];
    if (!caller) return;
    callerCount++;
    var row = document.createElement("div");
    row.className = "caller-row";
    row.title = caller.label;
    row.innerHTML =
      "<span class='caller-kind'>[" + e._kind + "]</span>" + caller.label;
    row.onclick = function() {{
      network.selectNodes([e.from]);
      showInfo(caller);
    }};
    list.appendChild(row);
  }});

  // show focus bar
  document.getElementById("focus-bar").style.display = "flex";
  document.getElementById("focus-label").textContent =
    target.label + " (" + target.group + ")";
  document.getElementById("focus-count").textContent =
    callerCount + " caller" + (callerCount !== 1 ? "s" : "");

  inFocusMode = true;
  closeInfo();
}}

function exitFocus() {{
  inFocusMode = false;
  document.getElementById("focus-bar").style.display = "none";
  document.getElementById("callers-panel").style.display = "none";
  document.getElementById("normal-filters").style.display = "block";
  applyFilters();
}}

// ── physics ───────────────────────────────────────────────────────────────────
function togglePhysics() {{
  physicsEnabled = !physicsEnabled;
  network.setOptions({{ physics: {{ enabled: physicsEnabled }} }});
  document.getElementById("physics-btn").textContent =
    physicsEnabled ? "Disable physics" : "Enable physics";
}}

// ── filters ───────────────────────────────────────────────────────────────────
function applyFilters() {{
  if (inFocusMode) return;
  var activeNodeKinds = new Set();
  document.querySelectorAll(".nf:checked").forEach(function(cb) {{
    activeNodeKinds.add(cb.value);
  }});
  var activeEdgeKinds = new Set();
  document.querySelectorAll(".ef:checked").forEach(function(cb) {{
    activeEdgeKinds.add(cb.value);
  }});

  var q = searchVal.toLowerCase();
  var visNodes = ALL_NODES.filter(function(n) {{
    if (!activeNodeKinds.has(n.group)) return false;
    if (q && n.label.toLowerCase().indexOf(q) === -1) return false;
    return true;
  }});
  var visIds = new Set(visNodes.map(function(n) {{ return n.id; }}));
  var visEdges = ALL_EDGES.filter(function(e) {{
    return visIds.has(e.from) && visIds.has(e.to) && activeEdgeKinds.has(e._kind);
  }});

  nodesDS.clear(); nodesDS.add(visNodes);
  edgesDS.clear(); edgesDS.add(visEdges);
  document.getElementById("s-nodes").textContent = visNodes.length;
  document.getElementById("s-edges").textContent = visEdges.length;
}}

function selectAll(checked) {{
  document.querySelectorAll(".nf, .ef").forEach(function(cb) {{
    cb.checked = checked;
  }});
  applyFilters();
}}

document.querySelectorAll(".nf, .ef").forEach(function(cb) {{
  cb.addEventListener("change", applyFilters);
}});

var searchTimer;
document.getElementById("search").addEventListener("input", function(e) {{
  searchVal = e.target.value;
  clearTimeout(searchTimer);
  if (inFocusMode) exitFocus();
  searchTimer = setTimeout(applyFilters, 200);
}});

// disable physics after stabilisation
network.on("stabilizationIterationsDone", function() {{
  if (physicsEnabled) {{
    network.setOptions({{ physics: {{ enabled: false }} }});
    physicsEnabled = false;
    document.getElementById("physics-btn").textContent = "Enable physics";
  }}
}});
</script>
</body>
</html>"""


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="visualize_graph",
        description="Build an interactive HTML code-graph visualisation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("root", help="Project root to analyse")
    parser.add_argument(
        "--lang",
        default="auto",
        metavar="LANG",
        help="auto | python | typescript | python,typescript (default: auto)",
    )
    parser.add_argument("--output", metavar="PATH", help="Output HTML file")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    parser.add_argument(
        "--show-external",
        action="store_true",
        help="Include stdlib/third_party external symbols",
    )
    parser.add_argument(
        "--show-structure",
        action="store_true",
        help="Include CONTAINS/DECLARES structural edges",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=1500,
        metavar="N",
        help="Prune low-degree nodes above this count (default: 1500)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        parser.error(f"Path does not exist: {root}")

    langs = _resolve_langs(args.lang, root)
    print(f"[graphlens] languages={langs}  root={root}")

    graph, elapsed = _run_analysis(root, langs)
    print(
        f"[graphlens] total: nodes={len(graph.nodes)}  "
        f"relations={len(graph.relations)}  elapsed={elapsed}s"
    )

    vis_nodes, vis_edges = _build_vis_data(
        graph,
        show_external=args.show_external,
        show_structure=args.show_structure,
        max_nodes=args.max_nodes,
    )
    print(f"[graphlens] rendering {len(vis_nodes)} nodes, {len(vis_edges)} edges")

    project_nodes = [n for n in graph.nodes.values() if n.kind == NodeKind.PROJECT]
    if project_nodes:
        project_name = " + ".join(sorted({n.name for n in project_nodes}))
    else:
        project_name = root.name

    stats = {
        "total_nodes":     len(graph.nodes),
        "total_relations": len(graph.relations),
        "vis_nodes":       len(vis_nodes),
        "vis_edges":       len(vis_edges),
        "lang":            ", ".join(langs),
        "elapsed":         elapsed,
    }

    html = _render_html(project_name, vis_nodes, vis_edges, stats)

    out_name = project_name.replace(" + ", "-").replace(" ", "_")
    out = Path(args.output) if args.output else Path(f"graph-{out_name}.html")
    out.write_text(html, encoding="utf-8")
    print(f"[graphlens] written → {out.resolve()}")

    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
        print("[graphlens] opened in browser")


if __name__ == "__main__":
    main()
