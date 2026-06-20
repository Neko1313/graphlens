"""Deterministic structural diff between two code graphs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphlens.models.graph import GraphLens
    from graphlens.models.nodes import Node
    from graphlens.models.relations import Relation


@dataclass(frozen=True)
class GraphDiff:
    """The difference from an old graph to a new graph."""

    added_nodes: list[Node]
    removed_nodes: list[Node]
    changed_nodes: list[tuple[Node, Node]]
    added_relations: list[Relation]
    removed_relations: list[Relation]

    @property
    def is_empty(self) -> bool:
        """Return True when the two graphs are structurally identical."""
        return not (
            self.added_nodes
            or self.removed_nodes
            or self.changed_nodes
            or self.added_relations
            or self.removed_relations
        )


def _relation_key(relation: Relation) -> tuple[str, str, str]:
    return (relation.source_id, relation.target_id, relation.kind.value)


def diff_graphs(old: GraphLens, new: GraphLens) -> GraphDiff:
    """Return a deterministic structural diff from ``old`` to ``new``."""
    old_ids = set(old.nodes)
    new_ids = set(new.nodes)
    added_nodes = [new.nodes[i] for i in sorted(new_ids - old_ids)]
    removed_nodes = [old.nodes[i] for i in sorted(old_ids - new_ids)]
    changed_nodes = [
        (old.nodes[i], new.nodes[i])
        for i in sorted(old_ids & new_ids)
        if old.nodes[i] != new.nodes[i]
    ]
    old_rels = {_relation_key(r): r for r in old.relations}
    new_rels = {_relation_key(r): r for r in new.relations}
    added_relations = [
        new_rels[k] for k in sorted(new_rels.keys() - old_rels.keys())
    ]
    removed_relations = [
        old_rels[k] for k in sorted(old_rels.keys() - new_rels.keys())
    ]
    return GraphDiff(
        added_nodes=added_nodes,
        removed_nodes=removed_nodes,
        changed_nodes=changed_nodes,
        added_relations=added_relations,
        removed_relations=removed_relations,
    )
