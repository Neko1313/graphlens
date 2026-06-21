"""Tests for Go cross-language boundary extraction (TCK-6)."""

from __future__ import annotations

from graphlens import (
    BoundaryRef,
    GraphLens,
    Node,
    NodeKind,
    RelationKind,
)
from graphlens.utils.span import Span

from graphlens_go._adapter import (
    _add_boundary,
    _extract_boundaries,
    _innermost_enclosing,
)
from graphlens_go._boundary import (
    GO_DEFAULT_BOUNDARY_EXTRACTORS,
    HttpClientExtractor,
    HttpServerExtractor,
    QueueExtractor,
    _string_content,
    _text,
)
from graphlens_go._visitor import (
    GoFileContext,
    GoStructureExtractor,
    parse_go,
)


def _root(code: str):
    return parse_go(code.encode()).root_node


def _find(node, type_):
    if node.type == type_:
        return node
    for child in node.children:
        found = _find(child, type_)
        if found is not None:
            return found
    return None


def _keys(refs, role=None):
    return {r.key for r in refs if role is None or r.role == role}


def _fn(code: str) -> str:
    return f"package m\nfunc f() {{ {code} }}\n"


# --------------------------------------------------------------------------
# Server extractor
# --------------------------------------------------------------------------


class TestServer:
    ex = HttpServerExtractor()

    def test_mechanism(self):
        assert self.ex.mechanism() == "http"

    def test_gin_route(self):
        refs = self.ex.extract(_root(_fn('r.GET("/u/:id", h)')))
        assert _keys(refs) == {"GET /u/{}"}
        assert refs[0].role == "server"

    def test_chi_route_lowercase_method(self):
        refs = self.ex.extract(_root(_fn('r.Post("/x", h)')))
        assert _keys(refs) == {"POST /x"}

    def test_http_object_is_not_server(self):
        assert self.ex.extract(_root(_fn('http.Get("/x")'))) == []

    def test_non_verb_method_ignored(self):
        assert self.ex.extract(_root(_fn('r.Use("/x", h)'))) == []

    def test_route_without_string_ignored(self):
        assert self.ex.extract(_root(_fn("r.GET(pathVar, h)"))) == []


# --------------------------------------------------------------------------
# Client extractor
# --------------------------------------------------------------------------


class TestClient:
    ex = HttpClientExtractor()

    def test_mechanism(self):
        assert self.ex.mechanism() == "http"

    def test_http_get_relative(self):
        refs = self.ex.extract(_root(_fn('http.Get("/api/x")')))
        assert _keys(refs) == {"GET /api/x"}
        assert refs[0].role == "client"

    def test_http_post_absolute(self):
        refs = self.ex.extract(_root(_fn('http.Post("http://h/api/y", b)')))
        assert _keys(refs) == {"POST /api/y"}

    def test_non_http_object_ignored(self):
        assert self.ex.extract(_root(_fn('r.Get("/x")'))) == []

    def test_non_verb_method_ignored(self):
        assert self.ex.extract(_root(_fn('http.NewRequest("GET", u)'))) == []

    def test_non_path_ignored(self):
        assert self.ex.extract(_root(_fn('http.Get("plainkey")'))) == []

    def test_empty_args_ignored(self):
        assert self.ex.extract(_root(_fn("http.Get()"))) == []


class TestQueue:
    ex = QueueExtractor()

    def test_mechanism(self):
        assert self.ex.mechanism() == "queue"

    def test_publish_is_producer(self):
        refs = self.ex.extract(_root(_fn('bus.Publish("orders", m)')))
        assert _keys(refs, "client") == {"orders"}

    def test_subscribe_is_consumer(self):
        refs = self.ex.extract(_root(_fn('c.Subscribe("orders")')))
        assert _keys(refs, "server") == {"orders"}

    def test_non_queue_method_skipped(self):
        assert self.ex.extract(_root(_fn('o.Send("x")'))) == []

    def test_non_string_topic_skipped(self):
        assert self.ex.extract(_root(_fn("bus.Publish(t, m)"))) == []


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class TestHelpers:
    def test_text_none(self):
        assert _text(None) == ""

    def test_string_content_interpreted(self):
        node = _find(_root(_fn('http.Get("/a")')), "interpreted_string_literal")
        assert _string_content(node) == "/a"

    def test_string_content_raw(self):
        node = _find(_root(_fn("http.Get(`/raw`)")), "raw_string_literal")
        assert _string_content(node) == "/raw"

    def test_string_content_empty_interpreted(self):
        node = _find(_root(_fn('http.Get("")')), "interpreted_string_literal")
        assert _string_content(node) == ""

    def test_string_content_empty_raw(self):
        node = _find(_root(_fn("http.Get(``)")), "raw_string_literal")
        assert _string_content(node) == ""

    def test_string_content_non_string(self):
        node = _find(_root("package m\nvar x = 1\n"), "int_literal")
        assert _string_content(node) is None


# --------------------------------------------------------------------------
# Adapter integration
# --------------------------------------------------------------------------


def _build(code: str) -> tuple[GraphLens, str, object]:
    graph = GraphLens()
    ctx = GoFileContext(
        project_name="p",
        package_qname="m/pkg",
        file_id="file1",
        file_rel="pkg/a.go",
    )
    root = parse_go(code.encode()).root_node
    GoStructureExtractor(graph, ctx, lambda _p: "stdlib").extract(root)
    return graph, "file1", root


def _rels(graph, kind):
    return [r for r in graph.relations if r.kind == kind]


def test_extract_server_and_client():
    code = (
        "package m\n"
        'func setup() { r.GET("/users/:id", h) }\n'
        'func load() { http.Get("/users/1") }\n'
    )
    graph, file_id, root = _build(code)
    _extract_boundaries(
        graph, [("pkg/a.go", file_id, root)],
        GO_DEFAULT_BOUNDARY_EXTRACTORS,
    )
    boundaries = graph.nodes_by_kind(NodeKind.BOUNDARY)
    assert len(boundaries) == 1
    assert boundaries[0].metadata["key"] == "GET /users/{}"
    assert len(_rels(graph, RelationKind.EXPOSES)) == 1
    assert len(_rels(graph, RelationKind.CONSUMES)) == 1


def test_no_extractors_is_noop():
    graph, file_id, root = _build(
        'package m\nfunc f() { r.GET("/x", h) }\n'
    )
    _extract_boundaries(graph, [("pkg/a.go", file_id, root)], [])
    assert graph.nodes_by_kind(NodeKind.BOUNDARY) == []


def _func(node_id: str, start: int, end: int) -> Node:
    return Node(
        id=node_id,
        kind=NodeKind.FUNCTION,
        qualified_name=node_id,
        name=node_id,
        file_path="pkg/a.go",
        span=Span(start, 1, end, 1),
    )


def test_innermost_enclosing_picks_deepest():
    outer = _func("outer", 1, 10)
    inner = _func("inner", 2, 5)
    assert _innermost_enclosing([outer, inner], 3, 1) == "inner"
    assert _innermost_enclosing([inner, outer], 3, 1) == "inner"


def test_innermost_enclosing_skips_non_containing():
    a = _func("a", 1, 2)
    b = _func("b", 5, 9)
    assert _innermost_enclosing([a, b], 7, 1) == "b"


def test_innermost_enclosing_no_candidates():
    assert _innermost_enclosing([], 1, 1) is None


def test_add_boundary_idempotent_node():
    graph = GraphLens()
    graph.add_node(_func("f", 1, 3))
    ref = BoundaryRef(
        mechanism="http", role="server", key="GET /x", line=1, col=1
    )
    _add_boundary(graph, "f", ref)
    _add_boundary(graph, "f", ref)
    assert len(graph.nodes_by_kind(NodeKind.BOUNDARY)) == 1
    assert len(_rels(graph, RelationKind.EXPOSES)) == 2
