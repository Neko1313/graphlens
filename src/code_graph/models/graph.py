"""In-memory code graph container."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from code_graph.exceptions import DuplicateNodeError

if TYPE_CHECKING:
    from code_graph.models.nodes import Node
    from code_graph.models.relations import Relation


@dataclass
class CodeGraph:
    """Accumulator for nodes and relations produced by language adapters."""

    nodes: dict[str, Node] = field(default_factory=dict)
    relations: list[Relation] = field(default_factory=list)

    def add_node(self, node: Node) -> None:
        """Add a node; raise DuplicateNodeError on ID collision."""
        if node.id in self.nodes:
            msg = f"Node with id '{node.id}' already exists"
            raise DuplicateNodeError(msg)
        self.nodes[node.id] = node

    def add_relation(self, relation: Relation) -> None:
        """Append a relation to the graph."""
        self.relations.append(relation)

    def merge(self, other: CodeGraph) -> None:
        """
        Merge another graph into this one.

        Raises DuplicateNodeError on ID collision.
        """
        for node in other.nodes.values():
            self.add_node(node)
        self.relations.extend(other.relations)
