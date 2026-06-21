"""Tests for the cross-language boundary linker."""

import pytest
from graphlens import (
    GraphLens,
    Node,
    NodeKind,
    Relation,
    RelationKind,
    make_boundary_id,
)

from graphlens_link import LinkResult, link_graph


def _fn(node_id: str, name: str) -> Node:
    return Node(
        id=node_id,
        kind=NodeKind.FUNCTION,
        qualified_name=name,
        name=name,
    )


def _boundary(mechanism: str, key: str) -> Node:
    return Node(
        id=make_boundary_id(mechanism, key),
        kind=NodeKind.BOUNDARY,
        qualified_name=f"{mechanism}:{key}",
        name=key,
        metadata={"mechanism": mechanism, "key": key},
    )


def _scenario(
    *,
    consumer_conf: object = 1.0,
    provider_conf: object = 1.0,
) -> tuple[GraphLens, Node]:
    """One provider, one consumer, one HTTP boundary between them."""
    graph = GraphLens()
    provider = _fn("prov", "server.handle_users")
    consumer = _fn("cons", "client.load_users")
    boundary = _boundary("http", "GET /users/{}")
    for node in (provider, consumer, boundary):
        graph.add_node(node)
    graph.add_relation(
        Relation(
            source_id=provider.id,
            target_id=boundary.id,
            kind=RelationKind.EXPOSES,
            metadata={"confidence": provider_conf},
        )
    )
    graph.add_relation(
        Relation(
            source_id=consumer.id,
            target_id=boundary.id,
            kind=RelationKind.CONSUMES,
            metadata={"confidence": consumer_conf},
        )
    )
    return graph, boundary


def _comm_edges(graph: GraphLens) -> list[Relation]:
    return [
        r
        for r in graph.relations
        if r.kind == RelationKind.COMMUNICATES_WITH
    ]


def test_links_consumer_to_provider() -> None:
    graph, boundary = _scenario()

    result = link_graph(graph)

    edges = _comm_edges(graph)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.source_id == "cons"
    assert edge.target_id == "prov"
    assert edge.metadata["mechanism"] == "http"
    assert edge.metadata["boundary_id"] == boundary.id
    assert edge.metadata["boundary_key"] == "GET /users/{}"
    assert edge.metadata["confidence"] == pytest.approx(1.0)
    assert result == LinkResult(
        relations_added=1,
        boundaries_total=1,
        boundaries_linked=1,
    )


def test_distinct_boundaries_same_pair_get_separate_edges() -> None:
    # A consumer and provider that share two different boundaries (e.g. two
    # queue topics) must yield two edges, not one collapsed by mechanism.
    graph = GraphLens()
    provider = _fn("prov", "server.handle")
    consumer = _fn("cons", "client.call")
    b1 = _boundary("queue", "orders.created")
    b2 = _boundary("queue", "orders.shipped")
    for node in (provider, consumer, b1, b2):
        graph.add_node(node)
    for b in (b1, b2):
        graph.add_relation(
            Relation(
                provider.id, b.id, RelationKind.EXPOSES,
                metadata={"confidence": 1.0},
            )
        )
        graph.add_relation(
            Relation(
                consumer.id, b.id, RelationKind.CONSUMES,
                metadata={"confidence": 1.0},
            )
        )

    result = link_graph(graph)

    edges = _comm_edges(graph)
    assert len(edges) == 2
    assert {e.metadata["boundary_key"] for e in edges} == {
        "orders.created",
        "orders.shipped",
    }
    assert result.relations_added == 2


def test_no_link_without_consumer() -> None:
    graph, _ = _scenario()
    # Drop the CONSUMES edge -> only a provider remains.
    graph.relations = [
        r for r in graph.relations if r.kind != RelationKind.CONSUMES
    ]

    result = link_graph(graph)

    assert _comm_edges(graph) == []
    assert result.boundaries_linked == 0
    assert result.boundaries_total == 1
    assert result.relations_added == 0


def test_no_link_without_provider() -> None:
    graph, _ = _scenario()
    graph.relations = [
        r for r in graph.relations if r.kind != RelationKind.EXPOSES
    ]

    result = link_graph(graph)

    assert _comm_edges(graph) == []
    assert result.boundaries_linked == 0


def test_idempotent() -> None:
    graph, _ = _scenario()

    first = link_graph(graph)
    second = link_graph(graph)

    assert first.relations_added == 1
    assert second.relations_added == 0
    assert len(_comm_edges(graph)) == 1


def test_min_confidence_filters_low_pairs() -> None:
    graph, _ = _scenario(consumer_conf=0.5, provider_conf=0.5)

    result = link_graph(graph, min_confidence=0.5)

    # combined confidence is 0.25 < 0.5 -> skipped
    assert _comm_edges(graph) == []
    assert result.relations_added == 0
    assert result.boundaries_linked == 1


def test_confidence_is_product() -> None:
    graph, _ = _scenario(consumer_conf=0.5, provider_conf=0.8)

    link_graph(graph)

    edge = _comm_edges(graph)[0]
    assert edge.metadata["confidence"] == pytest.approx(0.4)


@pytest.mark.parametrize(
    "value",
    [True, "high", None],
)
def test_non_numeric_confidence_defaults_to_one(value: object) -> None:
    graph, _ = _scenario(consumer_conf=value, provider_conf=value)

    link_graph(graph)

    edge = _comm_edges(graph)[0]
    assert edge.metadata["confidence"] == pytest.approx(1.0)


def test_integer_confidence_coerced() -> None:
    graph, _ = _scenario(consumer_conf=1, provider_conf=1)

    link_graph(graph)

    edge = _comm_edges(graph)[0]
    assert edge.metadata["confidence"] == pytest.approx(1.0)


def test_self_communication_skipped() -> None:
    """A node that both exposes and consumes the same boundary self-loops."""
    graph = GraphLens()
    node = _fn("same", "svc.proxy")
    boundary = _boundary("http", "GET /ping")
    graph.add_node(node)
    graph.add_node(boundary)
    graph.add_relation(
        Relation(node.id, boundary.id, RelationKind.EXPOSES)
    )
    graph.add_relation(
        Relation(node.id, boundary.id, RelationKind.CONSUMES)
    )

    result = link_graph(graph)

    assert _comm_edges(graph) == []
    assert result.boundaries_linked == 1
    assert result.relations_added == 0


def test_cartesian_product_of_sides() -> None:
    """Two consumers and two providers produce four edges."""
    graph = GraphLens()
    boundary = _boundary("queue", "orders.created")
    graph.add_node(boundary)
    for cid in ("c1", "c2"):
        graph.add_node(_fn(cid, cid))
        graph.add_relation(
            Relation(cid, boundary.id, RelationKind.CONSUMES)
        )
    for pid in ("p1", "p2"):
        graph.add_node(_fn(pid, pid))
        graph.add_relation(
            Relation(pid, boundary.id, RelationKind.EXPOSES)
        )

    result = link_graph(graph)

    assert result.relations_added == 4
    pairs = {(r.source_id, r.target_id) for r in _comm_edges(graph)}
    assert pairs == {
        ("c1", "p1"),
        ("c1", "p2"),
        ("c2", "p1"),
        ("c2", "p2"),
    }


def test_no_boundaries_is_noop() -> None:
    graph = GraphLens()
    graph.add_node(_fn("a", "a"))

    result = link_graph(graph)

    assert result == LinkResult(
        relations_added=0,
        boundaries_total=0,
        boundaries_linked=0,
    )
