"""Tests for Node and NodeKind models."""

import dataclasses

import pytest

from code_graph import Node, NodeKind
from code_graph.utils.ids import make_node_id
from code_graph.utils.span import Span


def _node(**kwargs) -> Node:
    defaults = {
        "id": make_node_id("proj", "proj.mod", NodeKind.MODULE.value),
        "kind": NodeKind.MODULE,
        "qualified_name": "proj.mod",
        "name": "mod",
    }
    defaults.update(kwargs)
    return Node(**defaults)  # type: ignore


class TestNodeKind:
    def test_all_values(self) -> None:
        expected = {
            "PROJECT", "MODULE", "FILE", "CLASS", "FUNCTION", "METHOD",
            "PARAMETER", "IMPORT", "DEPENDENCY", "SYMBOL", "EXTERNAL_SYMBOL",
        }
        assert {m.name for m in NodeKind} == expected

    def test_string_values(self) -> None:
        assert NodeKind.PROJECT.value == "project"
        assert NodeKind.MODULE.value == "module"
        assert NodeKind.FILE.value == "file"
        assert NodeKind.CLASS.value == "class"
        assert NodeKind.FUNCTION.value == "function"
        assert NodeKind.METHOD.value == "method"
        assert NodeKind.PARAMETER.value == "parameter"
        assert NodeKind.IMPORT.value == "import"
        assert NodeKind.DEPENDENCY.value == "dependency"
        assert NodeKind.SYMBOL.value == "symbol"
        assert NodeKind.EXTERNAL_SYMBOL.value == "external_symbol"

    def test_count(self) -> None:
        assert len(NodeKind) == 11


class TestNode:
    def test_creation_minimal(self) -> None:
        n = _node()
        assert n.kind == NodeKind.MODULE
        assert n.qualified_name == "proj.mod"
        assert n.name == "mod"
        assert n.file_path is None
        assert n.span is None
        assert n.metadata == {}

    def test_creation_with_all_fields(self) -> None:
        span = Span(1, 1, 5, 10)
        n = _node(file_path="src/mod.py", span=span, metadata={"key": "val"})
        assert n.file_path == "src/mod.py"
        assert n.span == span
        assert n.metadata == {"key": "val"}

    def test_frozen(self) -> None:
        n = _node()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            n.name = "other"  # type: ignore

    def test_equality(self) -> None:
        n1 = _node()
        n2 = _node()
        assert n1 == n2

    def test_different_ids_not_equal(self) -> None:
        n1 = _node(id="aaa", qualified_name="a", name="a")
        n2 = _node(id="bbb", qualified_name="b", name="b")
        assert n1 != n2

    def test_metadata_default_is_empty_dict(self) -> None:
        n = _node()
        assert isinstance(n.metadata, dict)
        assert len(n.metadata) == 0

    def test_with_span(self) -> None:
        span = Span(10, 1, 20, 50)
        n = _node(span=span)
        assert n.span is not None
        assert n.span.start_line == 10
        assert n.span.end_line == 20
