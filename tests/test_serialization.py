"""Tests for graph serialization (to/from dict and JSON)."""

import json

import pytest

from graphlens import (
    GraphLens,
    Node,
    NodeKind,
    Relation,
    RelationKind,
    SerializationError,
)
from graphlens.serialization import SCHEMA_VERSION
from graphlens.utils.span import Span


def _sample_graph() -> GraphLens:
    g = GraphLens()
    a = Node(
        id="a",
        kind=NodeKind.FUNCTION,
        qualified_name="proj.mod.foo",
        name="foo",
        file_path="/proj/mod.py",
        span=Span(1, 1, 5, 10),
        metadata={
            "origin": "internal",
            "name_span": Span(1, 5, 1, 8),
            "n": 3,
            "ok": True,
            "tags": ["x", "y"],
        },
    )
    b = Node(
        id="b",
        kind=NodeKind.EXTERNAL_SYMBOL,
        qualified_name="os.path.join",
        name="join",
        metadata={"origin": "stdlib"},
    )
    g.add_node(a)
    g.add_node(b)
    g.add_relation(
        Relation(
            source_id="a",
            target_id="b",
            kind=RelationKind.CALLS,
            metadata={"span": Span(2, 2, 2, 9), "access": "read"},
        )
    )
    g.metadata["resolver_status"] = "ok"
    return g


def test_dict_round_trip_preserves_everything() -> None:
    g = _sample_graph()
    restored = GraphLens.from_dict(g.to_dict())
    assert restored.nodes == g.nodes
    assert restored.relations == g.relations
    assert restored.metadata == g.metadata


def test_json_round_trip() -> None:
    g = _sample_graph()
    restored = GraphLens.from_json(g.to_json())
    assert restored.nodes == g.nodes
    assert restored.relations == g.relations


def test_to_dict_is_json_serializable_without_custom_encoder() -> None:
    text = json.dumps(_sample_graph().to_dict())
    assert '"schema_version"' in text


def test_span_round_trips_inside_metadata_and_field() -> None:
    restored = GraphLens.from_dict(_sample_graph().to_dict())
    rel = restored.relations[0]
    assert rel.metadata["span"] == Span(2, 2, 2, 9)
    node = restored.nodes["a"]
    assert node.metadata["name_span"] == Span(1, 5, 1, 8)
    assert node.span == Span(1, 1, 5, 10)


def test_to_dict_has_schema_version() -> None:
    assert _sample_graph().to_dict()["schema_version"] == SCHEMA_VERSION


def test_from_dict_rejects_unknown_schema_version() -> None:
    with pytest.raises(SerializationError, match="schema_version"):
        GraphLens.from_dict(
            {"schema_version": 999, "nodes": [], "relations": []}
        )


def test_from_dict_missing_schema_version_raises() -> None:
    with pytest.raises(SerializationError):
        GraphLens.from_dict({"nodes": [], "relations": []})


def test_from_dict_ignores_unknown_fields() -> None:
    g = _sample_graph()
    data = g.to_dict()
    data["future_top_level"] = {"x": 1}
    for nd in data["nodes"]:
        nd["future_field"] = "ignored"
    restored = GraphLens.from_dict(data)
    assert restored.nodes == g.nodes


def test_empty_graph_round_trip() -> None:
    restored = GraphLens.from_dict(GraphLens().to_dict())
    assert restored.nodes == {}
    assert restored.relations == []


def test_to_json_indent_produces_multiline() -> None:
    assert "\n" in _sample_graph().to_json(indent=2)
