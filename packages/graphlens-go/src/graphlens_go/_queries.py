"""Declarative tree-sitter query helper for Go (TCK-5)."""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from tree_sitter import Query, QueryCursor

from graphlens_go._visitor import _LANGUAGE

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode


@cache
def _compile(query_source: str) -> Query:
    return Query(_LANGUAGE, query_source)


def run_query(
    query_source: str, root: TSNode
) -> list[dict[str, list[TSNode]]]:
    """Run a query over ``root``; return one capture map per match."""
    cursor = QueryCursor(_compile(query_source))
    return [captures for _pattern, captures in cursor.matches(root)]
