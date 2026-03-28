"""GraphBackend contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from code_graph.models.graph import CodeGraph


class GraphBackend(ABC):
    """Contract for graph persistence backends."""

    @abstractmethod
    def store(self, graph: CodeGraph) -> None:
        """
        Persist the given graph.

        Implementation decides merge/replace semantics.
        """
        ...

    @abstractmethod
    def clear(self) -> None:
        """Remove all stored data."""
        ...
