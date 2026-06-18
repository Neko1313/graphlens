from graphlens import GraphLens, Node, NodeKind
from graphlens.utils import SpanIndex
from graphlens.utils.span import Span


def _node(node_id, full, name_span):
    return Node(
        id=node_id,
        kind=NodeKind.FUNCTION,
        qualified_name=node_id,
        name=node_id,
        file_path="/abs/mod.py",
        span=full,
        metadata={"name_span": name_span},
    )


def test_at_matches_name_span_not_keyword():
    # function spans lines 1-5; its name 'foo' sits at line 1 col 5
    g = GraphLens()
    g.add_node(_node("foo", Span(1, 1, 5, 1), Span(1, 5, 1, 8)))
    idx = SpanIndex.from_graph(g)
    assert idx.at("/abs/mod.py", 1, 5) == "foo"
    assert idx.at("/abs/mod.py", 3, 1) is None  # body, not the name


def test_enclosing_returns_innermost():
    g = GraphLens()
    g.add_node(_node("outer", Span(1, 1, 10, 1), Span(1, 5, 1, 10)))
    g.add_node(_node("inner", Span(4, 5, 6, 1), Span(4, 9, 4, 14)))
    idx = SpanIndex.from_graph(g)
    assert idx.enclosing("/abs/mod.py", 5, 5) == "inner"
    assert idx.enclosing("/abs/mod.py", 2, 1) == "outer"


def test_missing_file_returns_none():
    idx = SpanIndex()
    assert idx.at("/nope.py", 1, 1) is None
    assert idx.enclosing("/nope.py", 1, 1) is None


def test_manual_build_via_add_full_and_add_name():
    idx = SpanIndex()
    idx.add_full("/abs/m.py", "fn", Span(1, 1, 5, 1))
    idx.add_name("/abs/m.py", "fn", Span(1, 5, 1, 8))
    assert idx.enclosing("/abs/m.py", 3, 1) == "fn"
    assert idx.at("/abs/m.py", 1, 6) == "fn"
    assert idx.at("/abs/m.py", 3, 1) is None
