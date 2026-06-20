"""SymbolResolver: type-aware resolution backend contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from graphlens.status import ResolverStatus

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ResolvedRef:
    """A symbol resolved to its definition. Coordinates are 1-based."""

    full_name: str
    file_path: Path | None
    line: int
    col: int
    kind: str
    origin: str


@dataclass(frozen=True)
class Occurrence:
    """A single appearance of a symbol. Coordinates are 1-based."""

    file_path: Path
    line: int
    col: int
    is_definition: bool
    access: str


class SymbolResolver(ABC):
    """
    Resolves source positions to definitions for one language.

    Lets an adapter build precise CALLS/REFERENCES/HAS_TYPE/INHERITS_FROM
    edges. All coordinates are 1-based (line and column); an implementation
    converts to its engine's convention internally.
    """

    @abstractmethod
    def prepare(self, project_root: Path, files: list[Path]) -> None:
        """Set up the engine for a project before any queries."""
        ...

    @abstractmethod
    def definition_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        """Resolve the symbol at a position to its definition (cross-file)."""
        ...

    @abstractmethod
    def infer_type_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        """Infer the type of the expression at a position."""
        ...

    @abstractmethod
    def references_to(
        self, file: Path, line: int, col: int
    ) -> list[Occurrence]:
        """Return all references to the symbol at a position."""
        ...

    def status(self) -> ResolverStatus:
        """
        Report how completely the resolver ran after the last ``prepare``.

        Adapters record this on ``graph.metadata`` so callers can tell a
        structure-only (degraded) graph from a fully resolved one instead of
        silently trusting an incomplete result. Defaults to ``OK``;
        subprocess-backed resolvers override to return ``UNAVAILABLE`` when
        their engine failed to start.
        """
        return ResolverStatus.OK
