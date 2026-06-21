"""Tests for GraphLens query and indexing helpers."""

from helpers import make_node

from graphlens import GraphLens, Node, NodeKind, Relation, RelationKind


def _graph() -> tuple[GraphLens, dict[str, Node]]:
    g = GraphLens()
    foo = make_node(
        qname="proj.foo", name="foo", kind=NodeKind.FUNCTION, file_path="a.py"
    )
    bar = make_node(
        qname="proj.bar", name="bar", kind=NodeKind.FUNCTION, file_path="a.py"
    )
    baz = make_node(
        qname="proj.baz", name="baz", kind=NodeKind.FUNCTION, file_path="b.py"
    )
    var = make_node(
        qname="proj.V", name="V", kind=NodeKind.VARIABLE, file_path="a.py"
    )
    for n in (foo, bar, baz, var):
        g.add_node(n)
    g.add_relation(Relation(foo.id, bar.id, RelationKind.CALLS))
    g.add_relation(Relation(foo.id, baz.id, RelationKind.CALLS))
    g.add_relation(Relation(bar.id, var.id, RelationKind.REFERENCES))
    return g, {"foo": foo, "bar": bar, "baz": baz, "var": var}


def test_callees() -> None:
    g, n = _graph()
    assert {x.id for x in g.callees(n["foo"].id)} == {n["bar"].id, n["baz"].id}


def test_callers() -> None:
    g, n = _graph()
    assert {x.id for x in g.callers(n["bar"].id)} == {n["foo"].id}


def test_references_to() -> None:
    g, n = _graph()
    assert {x.id for x in g.references_to(n["var"].id)} == {n["bar"].id}


def test_outgoing_filtered_by_kind() -> None:
    g, n = _graph()
    assert len(g.outgoing(n["foo"].id, RelationKind.CALLS)) == 2
    assert g.outgoing(n["foo"].id, RelationKind.REFERENCES) == []


def test_incoming_all_kinds() -> None:
    g, n = _graph()
    assert len(g.incoming(n["var"].id)) == 1


def test_neighbors_depth_one() -> None:
    g, n = _graph()
    assert {x.id for x in g.neighbors(n["foo"].id, depth=1)} == {
        n["bar"].id,
        n["baz"].id,
    }


def test_neighbors_depth_two_reaches_transitive() -> None:
    g, n = _graph()
    assert {x.id for x in g.neighbors(n["foo"].id, depth=2)} == {
        n["bar"].id,
        n["baz"].id,
        n["var"].id,
    }


def test_neighbors_depth_zero_is_empty() -> None:
    g, n = _graph()
    assert g.neighbors(n["foo"].id, depth=0) == []


def test_nodes_by_kind() -> None:
    g, n = _graph()
    assert {x.id for x in g.nodes_by_kind(NodeKind.FUNCTION)} == {
        n["foo"].id,
        n["bar"].id,
        n["baz"].id,
    }


def test_nodes_in_file() -> None:
    g, n = _graph()
    assert {x.id for x in g.nodes_in_file("a.py")} == {
        n["foo"].id,
        n["bar"].id,
        n["var"].id,
    }


def test_nodes_by_name_short_and_qualified() -> None:
    g, n = _graph()
    assert [x.id for x in g.nodes_by_name("foo")] == [n["foo"].id]
    assert [x.id for x in g.nodes_by_name("proj.foo")] == [n["foo"].id]


def test_subgraph_for_file_includes_file_nodes_and_incident_edges() -> None:
    g, n = _graph()
    sub = g.subgraph_for_file("b.py")
    assert n["baz"].id in sub.nodes
    assert any(r.target_id == n["baz"].id for r in sub.relations)
    assert n["foo"].id in sub.nodes  # endpoint of incident edge pulled in


def test_index_rebuilds_after_add_relation() -> None:
    g, n = _graph()
    assert len(g.callees(n["foo"].id)) == 2  # build index
    new = make_node(qname="proj.qux", name="qux", kind=NodeKind.FUNCTION)
    g.add_node(new)
    g.add_relation(Relation(n["foo"].id, new.id, RelationKind.CALLS))
    assert len(g.callees(n["foo"].id)) == 3


def test_index_rebuilds_after_direct_append() -> None:
    g, n = _graph()
    assert len(g.callees(n["foo"].id)) == 2  # build index
    new = make_node(qname="proj.qux", name="qux", kind=NodeKind.FUNCTION)
    g.add_node(new)
    g.relations.append(Relation(n["foo"].id, new.id, RelationKind.CALLS))
    assert len(g.callees(n["foo"].id)) == 3
