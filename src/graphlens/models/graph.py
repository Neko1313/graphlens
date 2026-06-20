"""In-memory code graph with query, serialization, and diff support."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from graphlens.diffing import diff_graphs
from graphlens.exceptions import DuplicateNodeError
from graphlens.models.relations import RelationKind
from graphlens.serialization import (
    ensure_schema_version,
    graph_to_dict,
    graph_to_json,
    node_from_dict,
    relation_from_dict,
)
from graphlens.utils.serde import decode_metadata

if TYPE_CHECKING:
    from collections.abc import Iterable

    from graphlens.diffing import GraphDiff
    from graphlens.models.nodes import Node, NodeKind
    from graphlens.models.relations import Relation


@dataclass
class GraphLens:
    """Accumulator and query surface for a code graph."""

    nodes: dict[str, Node] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    _out_index: dict[str, list[Relation]] | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _in_index: dict[str, list[Relation]] | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _indexed_size: int = field(
        default=-1, init=False, repr=False, compare=False
    )

    # -- construction ----------------------------------------------------
    def add_node(self, node: Node) -> None:
        """Add a node; raise DuplicateNodeError on ID collision."""
        if node.id in self.nodes:
            msg = f"Node with id '{node.id}' already exists"
            raise DuplicateNodeError(msg)
        self.nodes[node.id] = node

    def add_relation(self, relation: Relation) -> None:
        """Append a relation and invalidate the edge indices."""
        self.relations.append(relation)
        self._out_index = None

    def merge(self, other: GraphLens) -> None:
        """Merge another graph into this one (raises on node ID collision)."""
        for node in other.nodes.values():
            self.add_node(node)
        self.relations.extend(other.relations)
        self.metadata.update(other.metadata)
        self._out_index = None

    # -- edge indices ----------------------------------------------------
    def _ensure_index(self) -> None:
        if (
            self._out_index is not None
            and self._indexed_size == len(self.relations)
        ):
            return
        out: dict[str, list[Relation]] = {}
        inc: dict[str, list[Relation]] = {}
        for rel in self.relations:
            out.setdefault(rel.source_id, []).append(rel)
            inc.setdefault(rel.target_id, []).append(rel)
        self._out_index = out
        self._in_index = inc
        self._indexed_size = len(self.relations)

    def outgoing(
        self, node_id: str, kind: RelationKind | None = None
    ) -> list[Relation]:
        """Return relations whose source is ``node_id``."""
        self._ensure_index()
        rels = (self._out_index or {}).get(node_id, [])
        if kind is None:
            return list(rels)
        return [r for r in rels if r.kind == kind]

    def incoming(
        self, node_id: str, kind: RelationKind | None = None
    ) -> list[Relation]:
        """Return relations whose target is ``node_id``."""
        self._ensure_index()
        rels = (self._in_index or {}).get(node_id, [])
        if kind is None:
            return list(rels)
        return [r for r in rels if r.kind == kind]

    # -- queries ---------------------------------------------------------
    def _resolve(self, ids: Iterable[str]) -> list[Node]:
        return [self.nodes[i] for i in ids if i in self.nodes]

    def callees(self, node_id: str) -> list[Node]:
        """Return nodes that ``node_id`` calls (outgoing CALLS targets)."""
        return self._resolve(
            r.target_id for r in self.outgoing(node_id, RelationKind.CALLS)
        )

    def callers(self, node_id: str) -> list[Node]:
        """Return nodes that call ``node_id`` (incoming CALLS sources)."""
        return self._resolve(
            r.source_id for r in self.incoming(node_id, RelationKind.CALLS)
        )

    def references_to(self, node_id: str) -> list[Node]:
        """Return nodes that reference ``node_id`` (incoming REFERENCES)."""
        return self._resolve(
            r.source_id
            for r in self.incoming(node_id, RelationKind.REFERENCES)
        )

    def neighbors(self, node_id: str, depth: int = 1) -> list[Node]:
        """Return distinct nodes within ``depth`` hops (any direction)."""
        seen = {node_id}
        frontier = [node_id]
        found: dict[str, Node] = {}
        for _ in range(max(depth, 0)):
            nxt: list[str] = []
            for nid in frontier:
                for rel in (*self.outgoing(nid), *self.incoming(nid)):
                    other = (
                        rel.target_id
                        if rel.source_id == nid
                        else rel.source_id
                    )
                    if other in seen:
                        continue
                    seen.add(other)
                    nxt.append(other)
                    node = self.nodes.get(other)
                    if node is not None:
                        found[other] = node
            frontier = nxt
        return list(found.values())

    def nodes_by_kind(self, kind: NodeKind) -> list[Node]:
        """Return all nodes of the given kind."""
        return [n for n in self.nodes.values() if n.kind == kind]

    def nodes_in_file(self, file_path: str) -> list[Node]:
        """Return all nodes declared in ``file_path``."""
        return [n for n in self.nodes.values() if n.file_path == file_path]

    def nodes_by_name(self, name: str) -> list[Node]:
        """Return nodes whose short or qualified name equals ``name``."""
        return [
            n
            for n in self.nodes.values()
            if name in (n.name, n.qualified_name)
        ]

    def subgraph(self, node_ids: Iterable[str]) -> GraphLens:
        """Return a new graph with these nodes and all incident relations."""
        ids = set(node_ids)
        sub = GraphLens()
        for nid in ids:
            node = self.nodes.get(nid)
            if node is not None:
                sub.add_node(node)
        for rel in self.relations:
            if rel.source_id not in ids and rel.target_id not in ids:
                continue
            for endpoint in (rel.source_id, rel.target_id):
                if endpoint not in sub.nodes and endpoint in self.nodes:
                    sub.add_node(self.nodes[endpoint])
            sub.add_relation(rel)
        return sub

    def subgraph_for_file(self, file_path: str) -> GraphLens:
        """Return a subgraph of all nodes in ``file_path`` and their edges."""
        return self.subgraph(n.id for n in self.nodes_in_file(file_path))

    # -- serialization / diff -------------------------------------------
    def to_dict(self) -> dict[str, object]:
        """Serialize the graph to a JSON-compatible dict."""
        return graph_to_dict(self)

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize the graph to a JSON string."""
        return graph_to_json(self, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> GraphLens:
        """Reconstruct a graph from :meth:`to_dict` output."""
        ensure_schema_version(data)
        graph = cls()
        graph.metadata.update(decode_metadata(data.get("metadata")))
        nodes = data.get("nodes")
        if isinstance(nodes, list):
            for nd in nodes:
                if isinstance(nd, dict):
                    graph.add_node(
                        node_from_dict({str(k): v for k, v in nd.items()})
                    )
        rels = data.get("relations")
        if isinstance(rels, list):
            for rd in rels:
                if isinstance(rd, dict):
                    graph.add_relation(
                        relation_from_dict(
                            {str(k): v for k, v in rd.items()}
                        )
                    )
        return graph

    @classmethod
    def from_json(cls, text: str) -> GraphLens:
        """Reconstruct a graph from :meth:`to_json` output."""
        return cls.from_dict(json.loads(text))

    def diff(self, other: GraphLens) -> GraphDiff:
        """Return the structural diff from this graph (old) to ``other``."""
        return diff_graphs(self, other)
