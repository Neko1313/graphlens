"""
Declarative tree-sitter query helper (TCK-5).

Lets the adapter express structural patterns as tree-sitter query strings
(the ``.scm`` S-expression language) and run them over a parsed tree,
instead of hand-written ``_visit_<type>`` branches.  Compiled queries are
cached by source so repeated use across files is cheap.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

from tree_sitter import Query, QueryCursor

from graphlens_python._visitor import _PY_LANGUAGE

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode


@cache
def _compile(query_source: str) -> Query:
    return Query(_PY_LANGUAGE, query_source)


def run_query(
    query_source: str, root: TSNode
) -> list[dict[str, list[TSNode]]]:
    """
    Run a tree-sitter query over ``root``.

    Returns one capture map per match: ``{capture_name: [nodes]}``.  Capture
    names are the ``@name`` tags in the query source.
    """
    cursor = QueryCursor(_compile(query_source))
    return [captures for _pattern, captures in cursor.matches(root)]
