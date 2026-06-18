"""Map a source position (file + 1-based line/col) back to a graph node."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphlens.models.graph import GraphLens
    from graphlens.utils.span import Span

_Entry = tuple[str, "Span"]  # (node_id, span)


def _contains(span: Span, line: int, col: int) -> bool:
    after_start = (line, col) >= (span.start_line, span.start_col)
    before_end = (line, col) <= (span.end_line, span.end_col)
    return after_start and before_end


def _area(span: Span) -> tuple[int, int]:
    # Smaller = tighter. Compare by (line spread, col spread).
    return (span.end_line - span.start_line, span.end_col - span.start_col)


class SpanIndex:
    """Per-file lists of (node_id, span); supports innermost/name lookups."""

    def __init__(self) -> None:
        self._full: dict[str, list[_Entry]] = {}
        self._name: dict[str, list[_Entry]] = {}

    def add_full(self, file_path: str, node_id: str, span: Span) -> None:
        self._full.setdefault(file_path, []).append((node_id, span))

    def add_name(self, file_path: str, node_id: str, name_span: Span) -> None:
        self._name.setdefault(file_path, []).append((node_id, name_span))

    @classmethod
    def from_graph(cls, graph: GraphLens) -> SpanIndex:
        idx = cls()
        for node in graph.nodes.values():
            if not isinstance(node.file_path, str) or node.span is None:
                continue
            idx.add_full(node.file_path, node.id, node.span)
            name_span = node.metadata.get("name_span")
            if name_span is not None:
                idx.add_name(node.file_path, node.id, name_span)  # type: ignore[arg-type]
        return idx

    def _smallest_containing(
        self,
        table: dict[str, list[_Entry]],
        file_path: str,
        line: int,
        col: int,
    ) -> str | None:
        best_id: str | None = None
        best_area: tuple[int, int] | None = None
        for node_id, span in table.get(file_path, ()):
            if not _contains(span, line, col):
                continue
            area = _area(span)
            if best_area is None or area < best_area:
                best_area, best_id = area, node_id
        return best_id

    def enclosing(self, file_path: str, line: int, col: int) -> str | None:
        """Return the id of the innermost node whose full span contains the
        given 1-based (line, col) position, or None if no node matches."""
        return self._smallest_containing(self._full, file_path, line, col)

    def at(self, file_path: str, line: int, col: int) -> str | None:
        """Return the id of the node whose name_span contains the given
        1-based (line, col) position, or None if no node matches."""
        return self._smallest_containing(self._name, file_path, line, col)
