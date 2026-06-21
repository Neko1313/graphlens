"""Tests for the graphlens analyze CLI command."""

from unittest.mock import patch

from graphlens import GraphLens, Node, NodeKind, Relation, RelationKind
from graphlens.utils.ids import make_node_id
from typer.testing import CliRunner

from graphlens_cli import app

runner = CliRunner()


def _graph_with_calls() -> GraphLens:
    g = GraphLens()
    fn_a = Node(
        id=make_node_id("p", "fn_a", NodeKind.FUNCTION.value),
        kind=NodeKind.FUNCTION,
        qualified_name="mod.fn_a",
        name="fn_a",
    )
    fn_b = Node(
        id=make_node_id("p", "fn_b", NodeKind.FUNCTION.value),
        kind=NodeKind.FUNCTION,
        qualified_name="mod.fn_b",
        name="fn_b",
    )
    ext = Node(
        id=make_node_id("p", "os.path", NodeKind.EXTERNAL_SYMBOL.value),
        kind=NodeKind.EXTERNAL_SYMBOL,
        qualified_name="os.path",
        name="path",
        metadata={"origin": "stdlib"},
    )
    g.add_node(fn_a)
    g.add_node(fn_b)
    g.add_node(ext)
    g.add_relation(Relation(source_id=fn_a.id, target_id=fn_b.id, kind=RelationKind.CALLS))
    return g


def test_analyze_prints_node_counts(tmp_path):
    g = _graph_with_calls()
    with (
        patch("graphlens_cli._analyze.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._analyze.run_analysis", return_value=(g, 0.1)),
    ):
        result = runner.invoke(app, ["analyze", str(tmp_path)])

    assert result.exit_code == 0
    assert "nodes" in result.output
    assert "function" in result.output


def test_analyze_prints_relation_kinds(tmp_path):
    g = _graph_with_calls()
    with (
        patch("graphlens_cli._analyze.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._analyze.run_analysis", return_value=(g, 0.1)),
    ):
        result = runner.invoke(app, ["analyze", str(tmp_path)])

    assert "calls" in result.output


def test_analyze_prints_external_origins(tmp_path):
    g = _graph_with_calls()
    with (
        patch("graphlens_cli._analyze.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._analyze.run_analysis", return_value=(g, 0.1)),
    ):
        result = runner.invoke(app, ["analyze", str(tmp_path)])

    assert "stdlib" in result.output


def test_analyze_prints_top_callers(tmp_path):
    g = _graph_with_calls()
    with (
        patch("graphlens_cli._analyze.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._analyze.run_analysis", return_value=(g, 0.1)),
    ):
        result = runner.invoke(app, ["analyze", str(tmp_path)])

    assert "caller" in result.output.lower()


def test_analyze_missing_root_exits(tmp_path):
    result = runner.invoke(app, ["analyze", str(tmp_path / "nonexistent")])
    assert result.exit_code != 0


def test_analyze_default_lang_is_auto(tmp_path):
    g = GraphLens()
    with (
        patch("graphlens_cli._analyze.resolve_langs", return_value=["python"]) as mock_resolve,
        patch("graphlens_cli._analyze.run_analysis", return_value=(g, 0.0)),
    ):
        runner.invoke(app, ["analyze", str(tmp_path)])

    mock_resolve.assert_called_once_with("auto", tmp_path)


def test_analyze_format_json_stdout(tmp_path):
    g = _graph_with_calls()
    with (
        patch("graphlens_cli._analyze.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._analyze.run_analysis", return_value=(g, 0.1)),
    ):
        result = runner.invoke(
            app, ["analyze", str(tmp_path), "--format", "json"]
        )
    assert result.exit_code == 0
    assert '"schema_version"' in result.output


def test_analyze_output_writes_json_file(tmp_path):
    g = _graph_with_calls()
    out = tmp_path / "graph.json"
    with (
        patch("graphlens_cli._analyze.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._analyze.run_analysis", return_value=(g, 0.1)),
    ):
        result = runner.invoke(app, ["analyze", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    restored = GraphLens.from_json(out.read_text())
    assert len(restored.nodes) == len(g.nodes)


def test_analyze_strict_exits_nonzero_when_not_ok(tmp_path):
    from graphlens import RESOLVER_STATUS_KEY

    g = _graph_with_calls()
    g.metadata[RESOLVER_STATUS_KEY] = "unavailable"
    with (
        patch("graphlens_cli._analyze.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._analyze.run_analysis", return_value=(g, 0.1)),
    ):
        result = runner.invoke(app, ["analyze", str(tmp_path), "--strict"])
    assert result.exit_code != 0


def test_analyze_strict_ok_exits_zero(tmp_path):
    from graphlens import RESOLVER_STATUS_KEY

    g = _graph_with_calls()
    g.metadata[RESOLVER_STATUS_KEY] = "ok"
    with (
        patch("graphlens_cli._analyze.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._analyze.run_analysis", return_value=(g, 0.1)),
    ):
        result = runner.invoke(app, ["analyze", str(tmp_path), "--strict"])
    assert result.exit_code == 0
