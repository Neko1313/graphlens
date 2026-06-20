"""Tests for graphlens_cli._neo4j helpers (driver mocked)."""

from unittest.mock import MagicMock, patch

from graphlens import GraphLens, Node, NodeKind, Relation, RelationKind
from graphlens.utils.ids import make_node_id
from graphlens.utils.span import Span
from typer.testing import CliRunner

from graphlens_cli import app
from graphlens_cli._neo4j import (
    _batches,
    _import_nodes,
    _import_relations,
    _node_label,
    _node_props,
    _rel_props,
)

runner = CliRunner()


def _fn(name: str) -> Node:
    return Node(
        id=make_node_id("p", name, NodeKind.FUNCTION.value),
        kind=NodeKind.FUNCTION,
        qualified_name=f"mod.{name}",
        name=name,
    )


def _ext(name: str) -> Node:
    return Node(
        id=make_node_id("p", name, NodeKind.EXTERNAL_SYMBOL.value),
        kind=NodeKind.EXTERNAL_SYMBOL,
        qualified_name=name,
        name=name,
        metadata={"origin": "stdlib"},
    )


# ---------------------------------------------------------------------------
# _node_props
# ---------------------------------------------------------------------------


def test_node_props_basic_fields():
    n = _fn("helper")
    props = _node_props(n)
    assert props["id"] == n.id
    assert props["kind"] == "function"
    assert props["name"] == "helper"
    assert props["qualified_name"] == "mod.helper"


def test_node_props_with_file_path():
    n = Node(
        id="x", kind=NodeKind.FUNCTION, qualified_name="fn", name="fn",
        file_path="/src/mod.py",
    )
    props = _node_props(n)
    assert props["file_path"] == "/src/mod.py"


def test_node_props_without_file_path():
    n = _fn("fn")
    props = _node_props(n)
    assert "file_path" not in props


def test_node_props_with_span():
    n = Node(
        id="x", kind=NodeKind.FUNCTION, qualified_name="fn", name="fn",
        span=Span(start_line=5, start_col=1, end_line=10, end_col=4),
    )
    props = _node_props(n)
    assert props["span_start_line"] == 5
    assert props["span_start_col"] == 1
    assert props["span_end_line"] == 10
    assert props["span_end_col"] == 4


def test_node_props_scalar_metadata_prefixed():
    n = Node(
        id="x", kind=NodeKind.IMPORT, qualified_name="os", name="os",
        metadata={"origin": "stdlib", "version": 3},
    )
    props = _node_props(n)
    assert props["meta_origin"] == "stdlib"
    assert props["meta_version"] == 3


def test_node_props_non_scalar_metadata_excluded():
    n = Node(
        id="x", kind=NodeKind.FUNCTION, qualified_name="fn", name="fn",
        metadata={"name_span": object(), "tags": ["a", "b"]},
    )
    props = _node_props(n)
    assert "meta_name_span" not in props
    assert "meta_tags" not in props


# ---------------------------------------------------------------------------
# _rel_props
# ---------------------------------------------------------------------------


def test_rel_props_empty_metadata():
    r = Relation(source_id="a", target_id="b", kind=RelationKind.CALLS)
    assert _rel_props(r) == {}


def test_rel_props_scalar_metadata():
    r = Relation(
        source_id="a", target_id="b", kind=RelationKind.CALLS,
        metadata={"weight": 2, "label": "direct"},
    )
    props = _rel_props(r)
    assert props["meta_weight"] == 2
    assert props["meta_label"] == "direct"


def test_rel_props_skips_non_scalar():
    r = Relation(
        source_id="a", target_id="b", kind=RelationKind.CALLS,
        metadata={"tags": ["x"]},
    )
    assert _rel_props(r) == {}


# ---------------------------------------------------------------------------
# _node_label
# ---------------------------------------------------------------------------


def test_node_label_function():
    n = _fn("fn")
    assert _node_label(n) == "Function"


def test_node_label_external_symbol():
    n = _ext("os")
    assert _node_label(n) == "ExternalSymbol"


def test_node_label_type_alias():
    n = Node(
        id="x", kind=NodeKind.TYPE_ALIAS, qualified_name="t", name="t",
    )
    assert _node_label(n) == "TypeAlias"


# ---------------------------------------------------------------------------
# _batches
# ---------------------------------------------------------------------------


def test_batches_splits_evenly():
    items = list(range(10))
    result = list(_batches(items, 3))
    assert result == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]


def test_batches_empty():
    assert list(_batches([], 5)) == []


def test_batches_smaller_than_size():
    assert list(_batches([1, 2], 10)) == [[1, 2]]


# ---------------------------------------------------------------------------
# _import_nodes
# ---------------------------------------------------------------------------


def test_import_nodes_calls_session_run():
    nodes = [_fn("a"), _fn("b")]
    mock_driver = MagicMock()
    mock_session = MagicMock()
    mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

    total = _import_nodes(mock_driver, nodes, batch_size=10)

    assert total == 2
    assert mock_session.run.called


def test_import_nodes_groups_by_label():
    nodes = [_fn("a"), _ext("os")]
    mock_driver = MagicMock()
    mock_session = MagicMock()
    mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

    _import_nodes(mock_driver, nodes, batch_size=100)

    # Two different cypher templates (Function vs ExternalSymbol)
    calls = mock_session.run.call_args_list
    cyphers = [c.args[0] for c in calls]
    assert any("Function" in c for c in cyphers)
    assert any("ExternalSymbol" in c for c in cyphers)


def test_import_nodes_batches_correctly():
    nodes = [_fn(f"fn{i}") for i in range(5)]
    mock_driver = MagicMock()
    mock_session = MagicMock()
    mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

    _import_nodes(mock_driver, nodes, batch_size=2)

    # 5 nodes in batches of 2 → 3 calls
    assert mock_session.run.call_count == 3


# ---------------------------------------------------------------------------
# _import_relations
# ---------------------------------------------------------------------------


def test_import_relations_groups_by_type():
    fn_a = _fn("a")
    fn_b = _fn("b")
    fn_c = _fn("c")
    rels = [
        Relation(source_id=fn_a.id, target_id=fn_b.id, kind=RelationKind.CALLS),
        Relation(source_id=fn_b.id, target_id=fn_c.id, kind=RelationKind.REFERENCES),
    ]
    mock_driver = MagicMock()
    mock_session = MagicMock()
    mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

    total = _import_relations(mock_driver, rels, batch_size=100)

    assert total == 2
    cyphers = [c.args[0] for c in mock_session.run.call_args_list]
    assert any("CALLS" in c for c in cyphers)
    assert any("REFERENCES" in c for c in cyphers)


def test_import_relations_empty():
    mock_driver = MagicMock()
    mock_session = MagicMock()
    mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)

    total = _import_relations(mock_driver, [], batch_size=100)

    assert total == 0
    mock_session.run.assert_not_called()


# ---------------------------------------------------------------------------
# neo4j CLI command — driver unavailable
# ---------------------------------------------------------------------------


def test_neo4j_command_exits_without_driver(tmp_path):
    with patch.dict("sys.modules", {"neo4j": None}):
        result = runner.invoke(app, ["neo4j", str(tmp_path)])
    assert result.exit_code != 0


def test_neo4j_command_exits_on_connection_error(tmp_path):
    mock_driver = MagicMock()
    mock_driver.verify_connectivity.side_effect = Exception("refused")

    # neo4j module mock: `from neo4j import GraphDatabase` → mock_neo4j.GraphDatabase
    mock_neo4j = MagicMock()
    mock_neo4j.GraphDatabase.driver.return_value = mock_driver

    g = GraphLens()
    with (
        patch("graphlens_cli._neo4j.resolve_langs", return_value=["python"]),
        patch("graphlens_cli._neo4j.run_analysis", return_value=(g, 0.1)),
        patch.dict("sys.modules", {"neo4j": mock_neo4j}),
    ):
        result = runner.invoke(
            app,
            ["neo4j", str(tmp_path), "--uri", "bolt://bad:7687"],
        )

    assert result.exit_code != 0
