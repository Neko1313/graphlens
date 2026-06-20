"""Tests for graph diffing."""

from helpers import make_node

from graphlens import GraphLens, Relation, RelationKind


def test_diff_added_and_removed_nodes() -> None:
    old = GraphLens()
    new = GraphLens()
    a = make_node(qname="proj.a", name="a")
    b = make_node(qname="proj.b", name="b")
    old.add_node(a)
    new.add_node(a)
    new.add_node(b)
    d = old.diff(new)
    assert [n.id for n in d.added_nodes] == [b.id]
    assert d.removed_nodes == []
    assert not d.is_empty


def test_diff_changed_node_by_metadata() -> None:
    old = GraphLens()
    new = GraphLens()
    old.add_node(make_node(qname="proj.a", name="a", metadata={"v": 1}))
    new.add_node(make_node(qname="proj.a", name="a", metadata={"v": 2}))
    d = old.diff(new)
    assert len(d.changed_nodes) == 1
    before, after = d.changed_nodes[0]
    assert before.metadata["v"] == 1
    assert after.metadata["v"] == 2


def test_diff_relations() -> None:
    old = GraphLens()
    new = GraphLens()
    a = make_node(qname="proj.a", name="a")
    b = make_node(qname="proj.b", name="b")
    for g in (old, new):
        g.add_node(a)
        g.add_node(b)
    old.add_relation(Relation(a.id, b.id, RelationKind.CALLS))
    new.add_relation(Relation(a.id, b.id, RelationKind.REFERENCES))
    d = old.diff(new)
    assert [r.kind for r in d.added_relations] == [RelationKind.REFERENCES]
    assert [r.kind for r in d.removed_relations] == [RelationKind.CALLS]


def test_diff_empty_when_identical() -> None:
    g1 = GraphLens()
    g2 = GraphLens()
    a = make_node()
    g1.add_node(a)
    g2.add_node(a)
    assert g1.diff(g2).is_empty


def test_diff_deterministic_regardless_of_relation_order() -> None:
    a = make_node(qname="proj.a", name="a")
    b = make_node(qname="proj.b", name="b")
    c = make_node(qname="proj.c", name="c")
    old = GraphLens()
    for n in (a, b, c):
        old.add_node(n)

    new1 = GraphLens()
    for n in (a, b, c):
        new1.add_node(n)
    new1.add_relation(Relation(a.id, b.id, RelationKind.CALLS))
    new1.add_relation(Relation(a.id, c.id, RelationKind.CALLS))

    new2 = GraphLens()
    for n in (a, b, c):
        new2.add_node(n)
    new2.add_relation(Relation(a.id, c.id, RelationKind.CALLS))
    new2.add_relation(Relation(a.id, b.id, RelationKind.CALLS))

    keys1 = [(r.source_id, r.target_id) for r in old.diff(new1).added_relations]
    keys2 = [(r.source_id, r.target_id) for r in old.diff(new2).added_relations]
    assert keys1 == keys2
