"""Tests for graphlens_cli._visualize: build_vis_data, render_html, command."""

from unittest.mock import patch

from graphlens import GraphLens, Node, NodeKind, Relation, RelationKind
from graphlens.utils.ids import make_node_id
from typer.testing import CliRunner

from graphlens_cli import app
from graphlens_cli._visualize import build_vis_data, render_html

runner = CliRunner()


def _simple_graph() -> GraphLens:
    g = GraphLens()
    proj = Node(
        id=make_node_id("p", "proj", NodeKind.PROJECT.value),
        kind=NodeKind.PROJECT,
        qualified_name="proj",
        name="proj",
    )
    fn = Node(
        id=make_node_id("p", "fn", NodeKind.FUNCTION.value),
        kind=NodeKind.FUNCTION,
        qualified_name="mod.fn",
        name="fn",
    )
    ext = Node(
        id=make_node_id("p", "ext", NodeKind.EXTERNAL_SYMBOL.value),
        kind=NodeKind.EXTERNAL_SYMBOL,
        qualified_name="os",
        name="os",
        metadata={"origin": "stdlib"},
    )
    g.add_node(proj)
    g.add_node(fn)
    g.add_node(ext)
    g.add_relation(Relation(source_id=fn.id, target_id=ext.id, kind=RelationKind.CALLS))
    return g


# ---------------------------------------------------------------------------
# build_vis_data
# ---------------------------------------------------------------------------


def test_build_vis_data_includes_non_external_by_default():
    g = _simple_graph()
    nodes, _edges = build_vis_data(g)
    node_ids = {n["id"] for n in nodes}
    ext_id = make_node_id("p", "ext", NodeKind.EXTERNAL_SYMBOL.value)
    assert ext_id not in node_ids  # external hidden by default


def test_build_vis_data_show_external():
    g = _simple_graph()
    nodes, _ = build_vis_data(g, show_external=True)
    node_ids = {n["id"] for n in nodes}
    ext_id = make_node_id("p", "ext", NodeKind.EXTERNAL_SYMBOL.value)
    assert ext_id in node_ids


def test_build_vis_data_hides_structural_edges_by_default():
    g = GraphLens()
    p = Node(
        id=make_node_id("p", "p", NodeKind.MODULE.value),
        kind=NodeKind.MODULE, qualified_name="p", name="p",
    )
    c = Node(
        id=make_node_id("p", "c", NodeKind.FUNCTION.value),
        kind=NodeKind.FUNCTION, qualified_name="c", name="c",
    )
    g.add_node(p)
    g.add_node(c)
    g.add_relation(Relation(source_id=p.id, target_id=c.id, kind=RelationKind.CONTAINS))
    _, edges = build_vis_data(g)
    assert not edges  # structural edges hidden by default


def test_build_vis_data_show_structure():
    g = GraphLens()
    p = Node(
        id=make_node_id("p", "p", NodeKind.MODULE.value),
        kind=NodeKind.MODULE, qualified_name="p", name="p",
    )
    c = Node(
        id=make_node_id("p", "c", NodeKind.FUNCTION.value),
        kind=NodeKind.FUNCTION, qualified_name="c", name="c",
    )
    g.add_node(p)
    g.add_node(c)
    g.add_relation(Relation(source_id=p.id, target_id=c.id, kind=RelationKind.CONTAINS))
    _, edges = build_vis_data(g, show_structure=True)
    assert len(edges) == 1


def test_build_vis_data_respects_max_nodes():
    g = GraphLens()
    for i in range(20):
        g.add_node(Node(
            id=make_node_id("p", f"fn{i}", NodeKind.FUNCTION.value),
            kind=NodeKind.FUNCTION,
            qualified_name=f"fn{i}",
            name=f"fn{i}",
        ))
    nodes, _ = build_vis_data(g, max_nodes=5)
    assert len(nodes) <= 5


def test_build_vis_data_pinned_structure_nodes_survive_pruning():
    g = GraphLens()
    proj = Node(
        id=make_node_id("p", "proj", NodeKind.PROJECT.value),
        kind=NodeKind.PROJECT, qualified_name="proj", name="proj",
    )
    g.add_node(proj)
    for i in range(20):
        g.add_node(Node(
            id=make_node_id("p", f"fn{i}", NodeKind.FUNCTION.value),
            kind=NodeKind.FUNCTION,
            qualified_name=f"fn{i}",
            name=f"fn{i}",
        ))
    nodes, _ = build_vis_data(g, max_nodes=3)
    node_ids = {n["id"] for n in nodes}
    assert proj.id in node_ids


def test_build_vis_data_node_has_required_fields():
    g = _simple_graph()
    nodes, _ = build_vis_data(g)
    for n in nodes:
        assert "id" in n
        assert "label" in n
        assert "color" in n
        assert "group" in n


def test_build_vis_data_edge_has_required_fields():
    g = _simple_graph()
    _, edges = build_vis_data(g, show_external=True)
    for e in edges:
        assert "from" in e
        assert "to" in e
        assert "_kind" in e


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------


def test_render_html_contains_vis_js():
    html = render_html("test", [], [], {})
    assert "vis-network" in html


def test_render_html_contains_project_name():
    html = render_html("myproject", [], [], {})
    assert "myproject" in html


def test_render_html_embeds_node_data():
    g = _simple_graph()
    nodes, edges = build_vis_data(g)
    html = render_html("proj", nodes, edges, {"total_nodes": 3})
    assert "ALL_NODES" in html
    assert "ALL_EDGES" in html


def test_render_html_shows_pruned_warning_when_fewer_vis_nodes():
    g = _simple_graph()
    nodes, edges = build_vis_data(g, max_nodes=1)
    html = render_html("proj", nodes, edges, {"total_nodes": 100})
    # pruned-warning block should be visible
    assert "display: block" in html


# ---------------------------------------------------------------------------
# visualize command
# ---------------------------------------------------------------------------


def test_visualize_command_writes_html(tmp_path):
    g = _simple_graph()
    out = tmp_path / "out.html"
    with (
        patch("graphlens_cli._visualize.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._visualize.run_analysis", return_value=(g, 0.1)),
        patch("graphlens_cli._visualize.webbrowser.open"),
    ):
        result = runner.invoke(
            app, ["visualize", str(tmp_path), "--output", str(out), "--no-open"]
        )

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "vis-network" in out.read_text()


def test_visualize_command_opens_browser_by_default(tmp_path):
    g = _simple_graph()
    out = tmp_path / "out.html"
    with (
        patch("graphlens_cli._visualize.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._visualize.run_analysis", return_value=(g, 0.1)),
        patch("graphlens_cli._visualize.webbrowser.open") as mock_open,
    ):
        runner.invoke(app, ["visualize", str(tmp_path), "--output", str(out)])

    mock_open.assert_called_once()


def test_visualize_command_no_open_skips_browser(tmp_path):
    g = _simple_graph()
    out = tmp_path / "out.html"
    with (
        patch("graphlens_cli._visualize.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._visualize.run_analysis", return_value=(g, 0.1)),
        patch("graphlens_cli._visualize.webbrowser.open") as mock_open,
    ):
        runner.invoke(
            app, ["visualize", str(tmp_path), "--output", str(out), "--no-open"]
        )

    mock_open.assert_not_called()
