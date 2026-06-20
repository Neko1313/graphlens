"""Tests for graphlens_cli._app shared helpers."""

from unittest.mock import MagicMock, patch

import pytest
import typer
from graphlens import GraphLens, Node, NodeKind, Relation, RelationKind
from graphlens.utils.ids import make_node_id

from graphlens_cli._app import (
    load_adapter,
    merge_graph,
    resolve_langs,
    run_analysis,
)

# ---------------------------------------------------------------------------
# merge_graph
# ---------------------------------------------------------------------------


def _make_node(name: str) -> Node:
    return Node(
        id=make_node_id("proj", name, NodeKind.FUNCTION.value),
        kind=NodeKind.FUNCTION,
        qualified_name=name,
        name=name,
    )


def test_merge_graph_combines_nodes():
    a = GraphLens()
    b = GraphLens()
    n1 = _make_node("fn_a")
    n2 = _make_node("fn_b")
    a.add_node(n1)
    b.add_node(n2)
    merge_graph(a, b)
    assert n1.id in a.nodes
    assert n2.id in a.nodes


def test_merge_graph_combines_relations():
    a = GraphLens()
    n1 = _make_node("fn_a")
    n2 = _make_node("fn_b")
    a.add_node(n1)
    a.add_node(n2)
    b = GraphLens()
    b.add_node(_make_node("fn_c"))
    rel = Relation(source_id=n1.id, target_id=n2.id, kind=RelationKind.CALLS)
    b.add_relation(rel)
    merge_graph(a, b)
    assert rel in a.relations


def test_merge_graph_skips_duplicate_nodes():
    a = GraphLens()
    b = GraphLens()
    n = _make_node("fn_x")
    a.add_node(n)
    b.add_node(n)  # same id
    merge_graph(a, b)  # must not raise DuplicateNodeError
    assert len(a.nodes) == 1


# ---------------------------------------------------------------------------
# resolve_langs
# ---------------------------------------------------------------------------


def test_resolve_langs_explicit_single(tmp_path):
    assert resolve_langs("python", tmp_path) == ["python"]


def test_resolve_langs_explicit_comma_separated(tmp_path):
    result = resolve_langs("python,typescript", tmp_path)
    assert result == ["python", "typescript"]


def test_resolve_langs_strips_whitespace(tmp_path):
    result = resolve_langs(" python , typescript ", tmp_path)
    assert result == ["python", "typescript"]


def test_resolve_langs_auto_matches_handlers(tmp_path):
    mock_cls = MagicMock()
    mock_cls.return_value.can_handle.return_value = True

    with patch("graphlens_cli._app.adapter_registry") as mock_reg:
        mock_reg.available.return_value = ["python"]
        mock_reg.load.return_value = mock_cls
        result = resolve_langs("auto", tmp_path)

    assert result == ["python"]


def test_resolve_langs_auto_no_adapters_raises(tmp_path):
    with patch("graphlens_cli._app.adapter_registry") as mock_reg:
        mock_reg.available.return_value = []
        with pytest.raises(typer.BadParameter):
            resolve_langs("auto", tmp_path)


def test_resolve_langs_auto_no_match_raises(tmp_path):
    mock_cls = MagicMock()
    mock_cls.return_value.can_handle.return_value = False

    with patch("graphlens_cli._app.adapter_registry") as mock_reg:
        mock_reg.available.return_value = ["python"]
        mock_reg.load.return_value = mock_cls
        with pytest.raises(typer.BadParameter):
            resolve_langs("auto", tmp_path)


# ---------------------------------------------------------------------------
# load_adapter
# ---------------------------------------------------------------------------


def test_load_adapter_uses_registry(tmp_path):
    mock_cls = MagicMock()
    mock_instance = MagicMock()
    mock_cls.return_value = mock_instance

    with patch("graphlens_cli._app.adapter_registry") as mock_reg:
        mock_reg.load.return_value = mock_cls
        result = load_adapter("python")

    assert result is mock_instance


def test_load_adapter_fallback_python():
    with patch("graphlens_cli._app.adapter_registry") as mock_reg:
        mock_reg.load.side_effect = Exception("not registered")
        adapter = load_adapter("python")
    from graphlens_python import PythonAdapter
    assert isinstance(adapter, PythonAdapter)


def test_load_adapter_unknown_raises():
    with patch("graphlens_cli._app.adapter_registry") as mock_reg:
        mock_reg.load.side_effect = Exception("not registered")
        with pytest.raises(typer.BadParameter, match="Unknown"):
            load_adapter("cobol")


# ---------------------------------------------------------------------------
# run_analysis
# ---------------------------------------------------------------------------


def test_run_analysis_calls_adapter_and_merges(tmp_path):
    g = GraphLens()
    g.add_node(_make_node("fn_z"))

    mock_adapter = MagicMock()
    mock_adapter.analyze.return_value = g

    with patch("graphlens_cli._app.load_adapter", return_value=mock_adapter):
        graph, elapsed = run_analysis(tmp_path, ["python"], verbose=False)

    assert len(graph.nodes) == 1
    assert elapsed >= 0
    mock_adapter.analyze.assert_called_once_with(tmp_path)


def test_run_analysis_merges_multiple_adapters(tmp_path):
    g1 = GraphLens()
    g1.add_node(_make_node("fn_a"))
    g2 = GraphLens()
    g2.add_node(_make_node("fn_b"))

    adapters = [MagicMock(), MagicMock()]
    adapters[0].analyze.return_value = g1
    adapters[1].analyze.return_value = g2

    with patch("graphlens_cli._app.load_adapter", side_effect=adapters):
        graph, _ = run_analysis(tmp_path, ["python", "typescript"], verbose=False)

    assert len(graph.nodes) == 2
