"""Source location utilities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Span:
    """A source location range. All values are 1-based."""

    start_line: int
    start_col: int
    end_line: int
    end_col: int
