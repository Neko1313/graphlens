"""
Declarative tree-sitter query helper for TypeScript (TCK-5).

Like the Python helper, but TypeScript ships two grammars (``.ts`` and
``.tsx``); a query must be compiled against the same language as the tree
it runs on, so callers pass a ``lang`` tag.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from tree_sitter import Query, QueryCursor

from graphlens_typescript._visitor import _TS_LANGUAGE, _TSX_LANGUAGE

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_LANGUAGES = {"ts": _TS_LANGUAGE, "tsx": _TSX_LANGUAGE}


@cache
def _compile(lang: str, query_source: str) -> Query:
    return Query(_LANGUAGES[lang], query_source)


def run_query(
    query_source: str, root: TSNode, lang: str = "ts"
) -> list[dict[str, list[TSNode]]]:
    """Run a query over ``root``; return one capture map per match."""
    cursor = QueryCursor(_compile(lang, query_source))
    return [captures for _pattern, captures in cursor.matches(root)]
