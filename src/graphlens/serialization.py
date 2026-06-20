"""
Serialize a :class:`GraphLens` to and from JSON-compatible structures.

This module never imports ``graph`` at module load (only for typing), so the
graph module can import these helpers at top level without a cycle.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from graphlens.exceptions import SerializationError
from graphlens.models.nodes import Node, NodeKind
from graphlens.models.relations import Relation, RelationKind
from graphlens.utils.serde import (
    decode_metadata,
    encode_metadata,
    span_from_list,
    span_to_list,
)

if TYPE_CHECKING:
    from graphlens.models.graph import GraphLens

SCHEMA_VERSION = 1


def node_to_dict(node: Node) -> dict[str, object]:
    """Serialize a node to a JSON-compatible dict."""
    return {
        "id": node.id,
        "kind": node.kind.value,
        "qualified_name": node.qualified_name,
        "name": node.name,
        "file_path": node.file_path,
        "span": span_to_list(node.span) if node.span is not None else None,
        "metadata": encode_metadata(node.metadata),
    }


def node_from_dict(data: dict[str, object]) -> Node:
    """Reconstruct a node from :func:`node_to_dict` output."""
    fp = data.get("file_path")
    return Node(
        id=str(data["id"]),
        kind=NodeKind(str(data["kind"])),
        qualified_name=str(data["qualified_name"]),
        name=str(data["name"]),
        file_path=fp if isinstance(fp, str) else None,
        span=span_from_list(data.get("span")),
        metadata=decode_metadata(data.get("metadata")),
    )


def relation_to_dict(relation: Relation) -> dict[str, object]:
    """Serialize a relation to a JSON-compatible dict."""
    return {
        "source_id": relation.source_id,
        "target_id": relation.target_id,
        "kind": relation.kind.value,
        "metadata": encode_metadata(relation.metadata),
    }


def relation_from_dict(data: dict[str, object]) -> Relation:
    """Reconstruct a relation from :func:`relation_to_dict` output."""
    return Relation(
        source_id=str(data["source_id"]),
        target_id=str(data["target_id"]),
        kind=RelationKind(str(data["kind"])),
        metadata=decode_metadata(data.get("metadata")),
    )


def graph_to_dict(graph: GraphLens) -> dict[str, object]:
    """Serialize a whole graph to a JSON-compatible dict."""
    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": encode_metadata(graph.metadata),
        "nodes": [node_to_dict(n) for n in graph.nodes.values()],
        "relations": [relation_to_dict(r) for r in graph.relations],
    }


def graph_to_json(graph: GraphLens, *, indent: int | None = None) -> str:
    """Serialize a whole graph to a JSON string."""
    return json.dumps(graph_to_dict(graph), indent=indent, ensure_ascii=False)


def ensure_schema_version(data: dict[str, object]) -> None:
    """Raise :class:`SerializationError` on an unsupported schema version."""
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        msg = (
            f"Unsupported graph schema_version {version!r}; "
            f"expected {SCHEMA_VERSION}"
        )
        raise SerializationError(msg)
