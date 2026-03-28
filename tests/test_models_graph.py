"""Tests for CodeGraph model."""

import pytest
from helpers import make_node, make_relation

from code_graph import CodeGraph, DuplicateNodeError, NodeKind, RelationKind


def test_empty_graph() -> None:
    g = CodeGraph()
    assert g.nodes == {}
    assert g.relations == []


def test_add_node() -> None:
    g = CodeGraph()
    n = make_node()
    g.add_node(n)
    assert n.id in g.nodes
    assert g.nodes[n.id] is n


def test_add_node_duplicate_raises() -> None:
    g = CodeGraph()
    n = make_node()
    g.add_node(n)
    with pytest.raises(DuplicateNodeError, match=n.id):
        g.add_node(n)


def test_add_relation() -> None:
    g = CodeGraph()
    a = make_node(qname="proj.a", name="a")
    b = make_node(qname="proj.b", name="b")
    g.add_node(a)
    g.add_node(b)
    rel = make_relation(a.id, b.id)
    g.add_relation(rel)
    assert rel in g.relations


def test_add_multiple_relations() -> None:
    g = CodeGraph()
    a = make_node(qname="proj.a", name="a")
    b = make_node(qname="proj.b", name="b")
    c = make_node(qname="proj.c", name="c", kind=NodeKind.CLASS)
    for n in (a, b, c):
        g.add_node(n)
    r1 = make_relation(a.id, b.id)
    r2 = make_relation(a.id, c.id, RelationKind.DECLARES)
    g.add_relation(r1)
    g.add_relation(r2)
    assert len(g.relations) == 2


def test_merge_success() -> None:
    g1 = CodeGraph()
    g2 = CodeGraph()
    a = make_node(qname="proj.a", name="a")
    b = make_node(qname="proj.b", name="b")
    rel = make_relation(a.id, b.id)

    g1.add_node(a)
    g2.add_node(b)
    g2.add_relation(rel)

    g1.merge(g2)

    assert a.id in g1.nodes
    assert b.id in g1.nodes
    assert rel in g1.relations


def test_merge_duplicate_node_raises() -> None:
    g1 = CodeGraph()
    g2 = CodeGraph()
    n = make_node()
    g1.add_node(n)
    g2.add_node(n)
    with pytest.raises(DuplicateNodeError):
        g1.merge(g2)


def test_merge_empty_into_non_empty() -> None:
    g1 = CodeGraph()
    g2 = CodeGraph()
    n = make_node()
    g1.add_node(n)
    g1.merge(g2)
    assert len(g1.nodes) == 1
    assert len(g1.relations) == 0


def test_merge_non_empty_into_empty() -> None:
    g1 = CodeGraph()
    g2 = CodeGraph()
    n = make_node()
    g2.add_node(n)
    g1.merge(g2)
    assert n.id in g1.nodes
