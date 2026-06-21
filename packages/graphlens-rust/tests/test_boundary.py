"""Tests for Rust cross-language boundary extraction (TCK-6)."""

from __future__ import annotations

from graphlens import (
    BoundaryRef,
    GraphLens,
    Node,
    NodeKind,
    RelationKind,
)
from graphlens.utils.span import Span

from graphlens_rust._adapter import (
    _add_boundary,
    _extract_boundaries,
    _innermost_enclosing,
)
from graphlens_rust._boundary import (
    RUST_DEFAULT_BOUNDARY_EXTRACTORS,
    HttpClientExtractor,
    HttpServerExtractor,
    QueueExtractor,
    _string_content,
    _text,
)
from graphlens_rust._visitor import (
    RustFileContext,
    RustStructureExtractor,
    parse_rust,
)


def _root(code: str):
    return parse_rust(code.encode()).root_node


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
    return f"fn f() {{ {code} }}\n"


# --------------------------------------------------------------------------
# Server extractor
# --------------------------------------------------------------------------


class TestServer:
    ex = HttpServerExtractor()

    def test_mechanism(self):
        assert self.ex.mechanism() == "http"

    def test_axum_route(self):
        code = _fn('let a = app.route("/u/:id", get(h));')
        refs = self.ex.extract(_root(code))
        assert _keys(refs) == {"GET /u/{}"}
        assert refs[0].role == "server"

    def test_axum_route_post(self):
        code = _fn('let a = app.route("/x", post(h));')
        assert _keys(self.ex.extract(_root(code))) == {"POST /x"}

    def test_route_handler_not_a_verb(self):
        code = _fn('let a = app.route("/x", middleware(h));')
        assert self.ex.extract(_root(code)) == []

    def test_route_handler_not_a_call(self):
        code = _fn('let a = app.route("/x", handler);')
        assert self.ex.extract(_root(code)) == []

    def test_route_missing_handler(self):
        code = _fn('let a = app.route("/x");')
        assert self.ex.extract(_root(code)) == []

    def test_route_path_not_string(self):
        code = _fn("let a = app.route(p, get(h));")
        assert self.ex.extract(_root(code)) == []

    def test_non_route_method_ignored(self):
        code = _fn('let a = app.nest("/x", r);')
        assert self.ex.extract(_root(code)) == []

    def test_actix_attribute(self):
        code = '#[get("/items")]\nasync fn list() {}\n'
        assert _keys(self.ex.extract(_root(code))) == {"GET /items"}

    def test_attribute_non_verb_ignored(self):
        code = '#[derive("Debug")]\nfn f() {}\n'
        assert self.ex.extract(_root(code)) == []

    def test_attribute_without_string_ignored(self):
        code = "#[get()]\nfn f() {}\n"
        assert self.ex.extract(_root(code)) == []


# --------------------------------------------------------------------------
# Client extractor
# --------------------------------------------------------------------------


class TestClient:
    ex = HttpClientExtractor()

    def test_mechanism(self):
        assert self.ex.mechanism() == "http"

    def test_reqwest_get(self):
        code = _fn('let r = client.get("/api/x");')
        refs = self.ex.extract(_root(code))
        assert _keys(refs) == {"GET /api/x"}
        assert refs[0].role == "client"

    def test_reqwest_post_absolute(self):
        code = _fn('let r = client.post("http://h/api/y");')
        assert _keys(self.ex.extract(_root(code))) == {"POST /api/y"}

    def test_non_verb_method_ignored(self):
        code = _fn('let r = client.send("/x");')
        assert self.ex.extract(_root(code)) == []

    def test_non_path_ignored(self):
        code = _fn('let v = map.get("key");')
        assert self.ex.extract(_root(code)) == []

    def test_empty_args_ignored(self):
        code = _fn("let r = client.get();")
        assert self.ex.extract(_root(code)) == []


class TestQueue:
    ex = QueueExtractor()

    def test_mechanism(self):
        assert self.ex.mechanism() == "queue"

    def test_publish_is_producer(self):
        code = _fn('let _ = bus.publish("orders", m);')
        assert _keys(self.ex.extract(_root(code)), "client") == {"orders"}

    def test_subscribe_is_consumer(self):
        code = _fn('let _ = c.subscribe("orders");')
        assert _keys(self.ex.extract(_root(code)), "server") == {"orders"}

    def test_non_queue_method_skipped(self):
        code = _fn('let _ = tx.send("x");')
        assert self.ex.extract(_root(code)) == []

    def test_non_string_topic_skipped(self):
        code = _fn("let _ = bus.publish(t, m);")
        assert self.ex.extract(_root(code)) == []


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class TestHelpers:
    def test_text_none(self):
        assert _text(None) == ""

    def test_string_content(self):
        node = _find(_root(_fn('let r = c.get("/a");')), "string_literal")
        assert _string_content(node) == "/a"

    def test_string_content_empty(self):
        node = _find(_root(_fn('let r = c.get("");')), "string_literal")
        assert _string_content(node) == ""

    def test_string_content_non_string(self):
        node = _find(_root("fn f() { let x = 1; }\n"), "integer_literal")
        assert _string_content(node) is None


# --------------------------------------------------------------------------
# Adapter integration
# --------------------------------------------------------------------------


def _build(code: str) -> tuple[GraphLens, str, object]:
    graph = GraphLens()
    ctx = RustFileContext(
        project_name="p",
        module_qname="crate::m",
        file_id="f1",
        file_rel="src/m.rs",
    )
    root = parse_rust(code.encode()).root_node
    RustStructureExtractor(graph, ctx, lambda _p: "stdlib").extract(root)
    return graph, "f1", root


def _rels(graph, kind):
    return [r for r in graph.relations if r.kind == kind]


def test_extract_server_and_client():
    code = (
        'fn setup() { let a = app.route("/users/:id", get(h)); }\n'
        'fn load() { let r = client.get("/users/1"); }\n'
    )
    graph, file_id, root = _build(code)
    _extract_boundaries(
        graph, [("src/m.rs", file_id, root)],
        RUST_DEFAULT_BOUNDARY_EXTRACTORS,
    )
    boundaries = graph.nodes_by_kind(NodeKind.BOUNDARY)
    assert len(boundaries) == 1
    assert boundaries[0].metadata["key"] == "GET /users/{}"
    assert len(_rels(graph, RelationKind.EXPOSES)) == 1
    assert len(_rels(graph, RelationKind.CONSUMES)) == 1


def test_no_extractors_is_noop():
    graph, file_id, root = _build('fn f() { app.route("/x", get(h)); }\n')
    _extract_boundaries(graph, [("src/m.rs", file_id, root)], [])
    assert graph.nodes_by_kind(NodeKind.BOUNDARY) == []


def _func(node_id: str, start: int, end: int) -> Node:
    return Node(
        id=node_id,
        kind=NodeKind.FUNCTION,
        qualified_name=node_id,
        name=node_id,
        file_path="src/m.rs",
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
        mechanism="http", role="client", key="GET /x", line=1, col=1
    )
    _add_boundary(graph, "f", ref)
    _add_boundary(graph, "f", ref)
    assert len(graph.nodes_by_kind(NodeKind.BOUNDARY)) == 1
    assert len(_rels(graph, RelationKind.CONSUMES)) == 2
