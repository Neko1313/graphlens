"""GraphBackend contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphlens.models.graph import GraphLens


class GraphBackend(ABC):
    """Contract for graph persistence backends."""

    @abstractmethod
    def store(self, graph: GraphLens) -> None:
        """
        Persist the given graph.

        Implementation decides merge/replace semantics.
        """
        ...

    @abstractmethod
    def clear(self) -> None:
        """Remove all stored data."""
        ...
