"""Tests for the MCP server query functions and subcommand (TCK-7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphlens import (
    RESOLVER_STATUS_KEY,
    GraphLens,
    Node,
    NodeKind,
    Relation,
    RelationKind,
    make_boundary_id,
)
from typer.testing import CliRunner

from graphlens_cli import _mcp, app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def _graph() -> GraphLens:
    g = GraphLens()
    caller = Node(
        id="c", kind=NodeKind.FUNCTION, qualified_name="m.caller", name="caller"
    )
    callee = Node(
        id="d", kind=NodeKind.FUNCTION, qualified_name="m.callee", name="callee"
    )
    var = Node(
        id="v", kind=NodeKind.VARIABLE, qualified_name="m.x", name="x"
    )
    boundary = Node(
        id=make_boundary_id("http", "GET /x"),
        kind=NodeKind.BOUNDARY,
        qualified_name="http:GET /x",
        name="GET /x",
        metadata={"mechanism": "http", "key": "GET /x"},
    )
    for n in (caller, callee, var, boundary):
        g.add_node(n)
    g.add_relation(Relation("c", "d", RelationKind.CALLS))
    g.add_relation(Relation("c", "v", RelationKind.REFERENCES))
    g.add_relation(Relation("d", boundary.id, RelationKind.EXPOSES))
    g.add_relation(Relation("c", boundary.id, RelationKind.CONSUMES))
    g.add_relation(
        Relation(
            "c",
            "d",
            RelationKind.COMMUNICATES_WITH,
            metadata={
                "mechanism": "http",
                "boundary_key": "GET /x",
                "confidence": 0.9,
            },
        )
    )
    g.metadata[RESOLVER_STATUS_KEY] = "ok"
    return g


def test_graph_stats() -> None:
    stats = _mcp.graph_stats(_graph())
    assert stats["nodes"] == 4
    assert stats["relations"] == 5
    assert stats["resolver_status"] == "ok"
    assert stats["nodes_by_kind"]["function"] == 2
    assert stats["relations_by_kind"]["calls"] == 1


def test_find_nodes() -> None:
    found = _mcp.find_nodes(_graph(), "caller")
    assert [n["qualified_name"] for n in found] == ["m.caller"]


def test_callers_by_name() -> None:
    result = _mcp.callers(_graph(), "callee")
    assert [n["qualified_name"] for n in result] == ["m.caller"]


def test_callers_by_id() -> None:
    result = _mcp.callers(_graph(), "d")
    assert [n["id"] for n in result] == ["c"]


def test_callees() -> None:
    result = _mcp.callees(_graph(), "caller")
    assert [n["qualified_name"] for n in result] == ["m.callee"]


def test_references() -> None:
    result = _mcp.references(_graph(), "x")
    assert [n["qualified_name"] for n in result] == ["m.caller"]


def test_neighbors() -> None:
    names = {n["qualified_name"] for n in _mcp.neighbors(_graph(), "caller")}
    assert "m.callee" in names


def test_communicates_with() -> None:
    edges = _mcp.communicates_with(_graph())
    assert edges == [
        {
            "consumer": "m.caller",
            "provider": "m.callee",
            "mechanism": "http",
            "key": "GET /x",
            "confidence": 0.9,
        }
    ]


def test_boundaries() -> None:
    bounds = _mcp.boundaries(_graph())
    assert len(bounds) == 1
    assert bounds[0]["mechanism"] == "http"
    assert bounds[0]["exposed_by"] == ["m.callee"]
    assert bounds[0]["consumed_by"] == ["m.caller"]


def test_load_graph_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    path.write_text(_graph().to_json(), encoding="utf-8")
    loaded = _mcp.load_graph(path)
    assert len(loaded.nodes) == 4


def test_mcp_command_without_mcp_package(tmp_path: Path) -> None:
    """Without the optional 'mcp' package the command exits with a hint."""
    path = tmp_path / "g.json"
    path.write_text(_graph().to_json(), encoding="utf-8")
    result = runner.invoke(app, ["mcp", "--graph", str(path)])
    assert result.exit_code == 1
    assert "mcp" in result.output.lower()
