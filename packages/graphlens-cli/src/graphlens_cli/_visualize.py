"""graphlens visualize — interactive HTML code-graph viewer."""

from __future__ import annotations

import json
import webbrowser
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Any

import typer
from graphlens import GraphLens, NodeKind, RelationKind

from graphlens_cli._app import app, resolve_langs, run_analysis

# ---------------------------------------------------------------------------
# Visual style constants
# ---------------------------------------------------------------------------

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
    "stdlib":      "#606060",
    "third_party": "#994444",
    "internal":    "#3A6080",
    "unknown":     "#555555",
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

_STRUCTURAL = {RelationKind.CONTAINS, RelationKind.DECLARES}

# ---------------------------------------------------------------------------
# Graph → vis.js data
# ---------------------------------------------------------------------------


def build_vis_data(
    graph: GraphLens,
    *,
    show_external: bool = False,
    show_structure: bool = False,
    max_nodes: int = 1500,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Convert a *graph* into vis.js-compatible node/edge dicts.

    Nodes above *max_nodes* are pruned by degree (lowest first), but
    PROJECT/MODULE/FILE nodes are always kept.
    """
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
            nid
            for nid in candidate_ids
            if nodes_map[nid].kind
            in (NodeKind.PROJECT, NodeKind.MODULE, NodeKind.FILE)
        }
        others = sorted(
            candidate_ids - pinned,
            key=lambda nid: degree[nid],
            reverse=True,
        )
        cap = max(0, max_nodes - len(pinned))
        candidate_ids = pinned | set(others[:cap])

    vis_nodes: list[dict[str, Any]] = []
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
            "<div style='font-family:monospace;max-width:320px'>"
            f"<b style='color:#FFD700'>{node.qualified_name}</b>"
            f"<br/><i style='color:#aaa'>{kind}</i>"
            f"{meta_lines}"
            f"{callers_btn}"
            "</div>"
        )

        label = node.name or node.qualified_name.split(".")[-1] or kind
        vis_nodes.append({
            "id":    nid,
            "label": label,
            "title": tooltip,
            "color": {
                "background": bg,
                "border":     "#222",
                "highlight":  {"background": bg, "border": "#fff"},
                "hover":      {"background": bg, "border": "#eee"},
            },
            "group": kind,
            "shape": _NODE_SHAPE.get(node.kind, "dot"),
            "font":  {"size": 12, "color": "#e0e0e0"},
        })

    vis_edges: list[dict[str, Any]] = []
    for idx, rel in enumerate(graph.relations):
        if (
            rel.source_id not in candidate_ids
            or rel.target_id not in candidate_ids
        ):
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
            "color":  {
                "color":     color,
                "highlight": color,
                "hover":     "#ffffff",
                "opacity":   0.75,
            },
            "arrows": "to",
            "width":  1 if is_structural else 2,
            "dashes": is_structural,
        })

    return vis_nodes, vis_edges


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def render_html(
    project_name: str,
    vis_nodes: list[dict[str, Any]],
    vis_edges: list[dict[str, Any]],
    stats: dict[str, Any],
) -> str:
    """Render a self-contained vis.js HTML page for the given graph data."""
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
            f"{k}</label>"
        )

    def edge_filter_row(k: str) -> str:
        color = _EDGE_COLOR.get(k, "#666")
        return (
            f'<label class="filter-row">'
            f'<input type="checkbox" class="ef" value="{k}" checked>'
            f'<span class="chip" style="background:{color}"></span>'
            f"{k}</label>"
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
#sidebar-header {{ padding: 12px; border-bottom: 1px solid #30363d; flex-shrink: 0; }}
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
#callers-panel {{ display: none; flex-direction: column; gap: 4px; }}
#callers-list {{ display: flex; flex-direction: column; gap: 1px;
                 max-height: 300px; overflow-y: auto; }}
.caller-row {{
  font-size: 11px; padding: 3px 6px; border-radius: 3px; cursor: pointer;
  color: #c9d1d9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.caller-row:hover {{ background: #21262d; }}
.caller-row .caller-kind {{ color: #E74C3C; font-size: 10px; margin-right: 4px; }}
#graph-wrap {{ flex: 1; display: flex; flex-direction: column; position: relative; }}
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
        <span class="k">Lang</span><span class="v">{stats.get("lang", "?")}</span>
        <span class="k">Time</span><span class="v">{stats.get("elapsed", 0):.2f}s</span>
      </div>
    </div>
    <div>
      <div class="section-title">Search</div>
      <input id="search" type="text" placeholder="Filter by label…"/>
    </div>
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
  nodes: {{ borderWidth: 1, size: 14, font: {{ size: 12, color: "#c9d1d9" }} }},
  edges: {{
    smooth: {{ type: "dynamic" }},
    font: {{ size: 10, color: "#8b949e", align: "middle" }},
    selectionWidth: 3,
  }},
  physics: {{
    enabled: true,
    stabilization: {{ iterations: 200, fit: true }},
    barnesHut: {{
      gravitationalConstant: -4000, springConstant: 0.04,
      springLength: 130, damping: 0.09,
    }},
  }},
  interaction: {{
    tooltipDelay: 150, hideEdgesOnDrag: true, multiselect: true, hover: true,
  }},
  layout: {{ improvedLayout: false }},
}};
var container = document.getElementById("graph");
var network = new vis.Network(container, {{ nodes: nodesDS, edges: edgesDS }}, options);
var physicsEnabled = true;
var inFocusMode = false;
var searchVal = "";
var nodeById = {{}};
ALL_NODES.forEach(function(n) {{ nodeById[n.id] = n; }});
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
function closeInfo() {{ document.getElementById("info-panel").style.display = "none"; }}
function showCallers(nodeId) {{
  var target = nodeById[nodeId];
  if (!target) return;
  var CALLER_KINDS = new Set(["calls", "references"]);
  var inEdges = ALL_EDGES.filter(function(e) {{
    return e.to === nodeId && CALLER_KINDS.has(e._kind);
  }});
  var callerIds = new Set(inEdges.map(function(e) {{ return e.from; }}));
  callerIds.add(nodeId);
  var focusNodes = ALL_NODES.filter(function(n) {{ return callerIds.has(n.id); }});
  nodesDS.clear(); nodesDS.add(focusNodes);
  edgesDS.clear(); edgesDS.add(inEdges);
  document.getElementById("s-nodes").textContent = focusNodes.length;
  document.getElementById("s-edges").textContent = inEdges.length;
  network.fit();
  document.getElementById("normal-filters").style.display = "none";
  document.getElementById("callers-panel").style.display = "flex";
  document.getElementById("callers-title").textContent = "Callers of " + target.label;
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
    row.innerHTML = "<span class='caller-kind'>[" + e._kind + "]</span>" + caller.label;
    row.onclick = function() {{ network.selectNodes([e.from]); showInfo(caller); }};
    list.appendChild(row);
  }});
  document.getElementById("focus-bar").style.display = "flex";
  document.getElementById("focus-label").textContent = target.label + " (" + target.group + ")";
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
function togglePhysics() {{
  physicsEnabled = !physicsEnabled;
  network.setOptions({{ physics: {{ enabled: physicsEnabled }} }});
  document.getElementById("physics-btn").textContent =
    physicsEnabled ? "Disable physics" : "Enable physics";
}}
function applyFilters() {{
  if (inFocusMode) return;
  var activeNodeKinds = new Set();
  document.querySelectorAll(".nf:checked").forEach(function(cb) {{ activeNodeKinds.add(cb.value); }});
  var activeEdgeKinds = new Set();
  document.querySelectorAll(".ef:checked").forEach(function(cb) {{ activeEdgeKinds.add(cb.value); }});
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
  document.querySelectorAll(".nf, .ef").forEach(function(cb) {{ cb.checked = checked; }});
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


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@app.command()
def visualize(
    root: Annotated[
        Path,
        typer.Argument(
            help="Project root to analyse",
            exists=True,
            file_okay=False,
            resolve_path=True,
        ),
    ],
    lang: Annotated[
        str,
        typer.Option(
            help="Adapter(s): auto | python | typescript | python,typescript",
            show_default=True,
        ),
    ] = "auto",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output HTML file (default: graph-<name>.html)"),
    ] = None,
    no_open: Annotated[
        bool,
        typer.Option("--no-open", help="Do not open the browser automatically"),
    ] = False,
    show_external: Annotated[
        bool,
        typer.Option("--show-external", help="Include stdlib/third_party external symbol nodes"),
    ] = False,
    show_structure: Annotated[
        bool,
        typer.Option("--show-structure", help="Include CONTAINS/DECLARES structural edges"),
    ] = False,
    max_nodes: Annotated[
        int,
        typer.Option("--max-nodes", help="Prune low-degree nodes above this count", min=1),
    ] = 1500,
) -> None:
    """Build an interactive HTML code-graph visualisation."""
    langs = resolve_langs(lang, root)
    typer.echo(f"Analysing {root}  [lang={', '.join(langs)}]")
    graph, elapsed = run_analysis(root, langs)
    typer.echo(
        f"  {len(graph.nodes)} nodes, {len(graph.relations)} relations"
        f"  ({elapsed:.2f}s)"
    )

    vis_nodes, vis_edges = build_vis_data(
        graph,
        show_external=show_external,
        show_structure=show_structure,
        max_nodes=max_nodes,
    )
    typer.echo(f"  rendering {len(vis_nodes)} nodes, {len(vis_edges)} edges")

    project_nodes = [n for n in graph.nodes.values() if n.kind == NodeKind.PROJECT]
    project_name = (
        " + ".join(sorted({n.name for n in project_nodes}))
        if project_nodes
        else root.name
    )

    stats: dict[str, Any] = {
        "total_nodes":     len(graph.nodes),
        "total_relations": len(graph.relations),
        "lang":            ", ".join(langs),
        "elapsed":         elapsed,
    }

    html = render_html(project_name, vis_nodes, vis_edges, stats)

    out_name = project_name.replace(" + ", "-").replace(" ", "_")
    out = output or Path(f"graph-{out_name}.html")
    out.write_text(html, encoding="utf-8")
    typer.echo(f"  written → {out.resolve()}")

    if not no_open:
        webbrowser.open(out.resolve().as_uri())
