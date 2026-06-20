"""Tests for the graphlens query CLI command."""

from graphlens import GraphLens, Node, NodeKind, Relation, RelationKind
from typer.testing import CliRunner

from graphlens_cli import app

runner = CliRunner()


def _write_graph(path):
    g = GraphLens()
    g.add_node(
        Node(id="a", kind=NodeKind.FUNCTION, qualified_name="mod.run", name="run")
    )
    g.add_node(
        Node(
            id="b",
            kind=NodeKind.FUNCTION,
            qualified_name="mod.helper",
            name="helper",
        )
    )
    g.add_relation(Relation(source_id="a", target_id="b", kind=RelationKind.CALLS))
    path.write_text(g.to_json())


def test_query_callers(tmp_path):
    gp = tmp_path / "g.json"
    _write_graph(gp)
    result = runner.invoke(
        app, ["query", "helper", "--graph", str(gp), "--op", "callers"]
    )
    assert result.exit_code == 0
    assert "mod.run" in result.output


def test_query_callees(tmp_path):
    gp = tmp_path / "g.json"
    _write_graph(gp)
    result = runner.invoke(
        app, ["query", "run", "--graph", str(gp), "--op", "callees"]
    )
    assert result.exit_code == 0
    assert "mod.helper" in result.output


def test_query_by_node_id(tmp_path):
    gp = tmp_path / "g.json"
    _write_graph(gp)
    result = runner.invoke(
        app, ["query", "b", "--graph", str(gp), "--op", "callers"]
    )
    assert result.exit_code == 0
    assert "mod.run" in result.output


def test_query_neighbors(tmp_path):
    gp = tmp_path / "g.json"
    _write_graph(gp)
    result = runner.invoke(
        app, ["query", "run", "--graph", str(gp), "--op", "neighbors"]
    )
    assert result.exit_code == 0
    assert "mod.helper" in result.output


def test_query_unknown_node_exits_nonzero(tmp_path):
    gp = tmp_path / "g.json"
    _write_graph(gp)
    result = runner.invoke(app, ["query", "nope", "--graph", str(gp)])
    assert result.exit_code != 0


def test_query_invalid_operation(tmp_path):
    gp = tmp_path / "g.json"
    _write_graph(gp)
    result = runner.invoke(
        app, ["query", "helper", "--graph", str(gp), "--op", "bogus"]
    )
    assert result.exit_code != 0


def test_query_empty_results_prints_none(tmp_path):
    gp = tmp_path / "g.json"
    _write_graph(gp)
    result = runner.invoke(
        app, ["query", "helper", "--graph", str(gp), "--op", "callees"]
    )
    assert result.exit_code == 0
    assert "(none)" in result.output
