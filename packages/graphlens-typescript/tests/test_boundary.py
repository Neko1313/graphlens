"""Tests for TypeScript cross-language boundary extraction (TCK-6)."""

from __future__ import annotations

from pathlib import Path

from graphlens import GraphLens, Node, NodeKind, RelationKind
from graphlens.utils.ids import make_node_id

from graphlens_typescript._adapter import (
    _add_boundary,
    _extract_boundaries,
    _innermost_enclosing,
)
from graphlens_typescript._boundary import (
    TYPESCRIPT_DEFAULT_BOUNDARY_EXTRACTORS,
    HttpClientExtractor,
    HttpServerExtractor,
    QueueExtractor,
    _text,
    _url_template,
)
from graphlens_typescript._visitor import (
    ImportClassifier,
    TypescriptASTVisitor,
    VisitorContext,
    parse_typescript,
)


def _root(code: str):
    return parse_typescript(code.encode()).root_node


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


# --------------------------------------------------------------------------
# HTTP server extractor
# --------------------------------------------------------------------------


class TestHttpServer:
    ex = HttpServerExtractor()

    def test_mechanism(self):
        assert self.ex.mechanism() == "http"

    def test_express_route(self):
        refs = self.ex.extract(_root('app.get("/u/:id", h);'), "ts")
        assert _keys(refs) == {"GET /u/{}"}
        assert refs[0].role == "server"

    def test_router_post(self):
        refs = self.ex.extract(_root('router.post("/x", h);'), "ts")
        assert _keys(refs) == {"POST /x"}

    def test_nest_decorator(self):
        code = 'class C { @Get("/items") list() {} }'
        assert _keys(self.ex.extract(_root(code), "ts")) == {"GET /items"}

    def test_non_server_object_ignored(self):
        assert self.ex.extract(_root('foo.get("/x", h);'), "ts") == []

    def test_non_verb_method_ignored(self):
        assert self.ex.extract(_root('app.use("/x", h);'), "ts") == []

    def test_route_without_string_ignored(self):
        assert self.ex.extract(_root("app.get(path, h);"), "ts") == []

    def test_settings_getter_ignored(self):
        # `app.get("view engine")` is an Express settings getter, not a route.
        assert self.ex.extract(_root('app.get("view engine");'), "ts") == []

    def test_non_verb_decorator_ignored(self):
        assert self.ex.extract(_root("class C { @Injectable() m(){} }"), "ts") == []

    def test_decorator_without_url_ignored(self):
        assert self.ex.extract(_root("class C { @Get() m(){} }"), "ts") == []


# --------------------------------------------------------------------------
# HTTP client extractor
# --------------------------------------------------------------------------


class TestHttpClient:
    ex = HttpClientExtractor()

    def test_mechanism(self):
        assert self.ex.mechanism() == "http"

    def test_fetch_relative(self):
        refs = self.ex.extract(_root('fetch("/api/x");'), "ts")
        assert _keys(refs) == {"GET /api/x"}
        assert refs[0].role == "client"

    def test_fetch_method_from_options(self):
        refs = self.ex.extract(
            _root('fetch("/api/x", {method: "POST"});'), "ts"
        )
        assert _keys(refs) == {"POST /api/x"}

    def test_fetch_method_non_literal_defaults_get(self):
        refs = self.ex.extract(
            _root('fetch("/api/x", {method: verb});'), "ts"
        )
        assert _keys(refs) == {"GET /api/x"}

    def test_fetch_options_without_method_defaults_get(self):
        refs = self.ex.extract(
            _root('fetch("/api/x", {headers: {}});'), "ts"
        )
        assert _keys(refs) == {"GET /api/x"}

    def test_fetch_empty_method_defaults_get(self):
        refs = self.ex.extract(_root('fetch("/api/x", {method: ""});'), "ts")
        assert _keys(refs) == {"GET /api/x"}

    def test_fetch_spread_options_defaults_get(self):
        refs = self.ex.extract(_root('fetch("/api/x", {...opts});'), "ts")
        assert _keys(refs) == {"GET /api/x"}

    def test_fetch_template_string(self):
        refs = self.ex.extract(_root("fetch(`/u/${id}`);"), "ts")
        assert _keys(refs) == {"GET /u/{}"}

    def test_axios_post_absolute(self):
        refs = self.ex.extract(_root('axios.post("http://h/api/y", b);'), "ts")
        assert _keys(refs) == {"POST /api/y"}

    def test_fetch_non_string_ignored(self):
        assert self.ex.extract(_root("fetch(u);"), "ts") == []

    def test_fetch_non_path_ignored(self):
        assert self.ex.extract(_root('fetch("notapath");'), "ts") == []

    def test_non_fetch_identifier_call_ignored(self):
        assert self.ex.extract(_root('doThing("/x");'), "ts") == []

    def test_axios_non_path_ignored(self):
        assert self.ex.extract(_root('axios.get("plainkey");'), "ts") == []

    def test_non_client_object_ignored(self):
        assert self.ex.extract(_root('svc.get("/x");'), "ts") == []

    def test_axios_non_verb_ignored(self):
        assert self.ex.extract(_root('axios.create("/x");'), "ts") == []


class TestQueue:
    ex = QueueExtractor()

    def test_mechanism(self):
        assert self.ex.mechanism() == "queue"

    def test_publish_is_producer(self):
        refs = self.ex.extract(_root('bus.publish("orders", m);'), "ts")
        assert _keys(refs, "client") == {"orders"}

    def test_subscribe_is_consumer(self):
        refs = self.ex.extract(_root('bus.subscribe("orders");'), "ts")
        assert _keys(refs, "server") == {"orders"}

    def test_non_queue_method_skipped(self):
        assert self.ex.extract(_root('obj.send("x");'), "ts") == []

    def test_rxjs_subscribe_callback_skipped(self):
        assert self.ex.extract(_root("obs.subscribe(fn);"), "ts") == []


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class TestHelpers:
    def test_text_none(self):
        assert _text(None) == ""

    def test_url_template_string(self):
        node = _find(_root('const x = "/a/b";'), "string")
        assert _url_template(node) == "/a/b"

    def test_url_template_empty_string(self):
        node = _find(_root('const x = "";'), "string")
        assert _url_template(node) == ""

    def test_url_template_template_string(self):
        node = _find(_root("const x = `/u/${i}`;"), "template_string")
        assert _url_template(node) == "/u/{}"

    def test_url_template_non_string(self):
        node = _find(_root("const x = 1;"), "number")
        assert _url_template(node) is None


# --------------------------------------------------------------------------
# Adapter integration (visitor-built graph; no resolver)
# --------------------------------------------------------------------------


def _visit(code: str, rel_path: str = "src/app.ts"):
    graph = GraphLens()
    abs_path = Path("/").resolve() / rel_path
    file_id = make_node_id("p", rel_path, NodeKind.FILE.value)
    graph.add_node(
        Node(
            id=file_id,
            kind=NodeKind.FILE,
            qualified_name=rel_path,
            name="app.ts",
            file_path=rel_path,
        )
    )
    classifier = ImportClassifier(
        stdlib=frozenset(),
        third_party=frozenset(),
        internal=frozenset(),
    )
    ctx = VisitorContext(
        project_name="p",
        file_path=abs_path,
        file_relative_path=rel_path,
        source_root=Path("src"),
        module_qualified_name="app",
    )
    tree = parse_typescript(code.encode())
    TypescriptASTVisitor(
        ctx, graph, file_id, code.encode(), classifier
    ).visit(tree.root_node)
    return graph, file_id, tree.root_node, abs_path


def _rels(graph, kind):
    return [r for r in graph.relations if r.kind == kind]


def test_extract_server_and_client():
    code = (
        "function setup() {\n"
        '  app.get("/users/:id", handler);\n'
        "}\n"
        "async function load() {\n"
        '  await fetch("/users/1");\n'
        "}\n"
    )
    graph, _file_id, root, fp = _visit(code)
    _extract_boundaries(
        graph, [(fp, _file_id, root, "ts")],
        TYPESCRIPT_DEFAULT_BOUNDARY_EXTRACTORS,
    )
    boundaries = graph.nodes_by_kind(NodeKind.BOUNDARY)
    assert len(boundaries) == 1
    assert boundaries[0].metadata["key"] == "GET /users/{}"
    assert len(_rels(graph, RelationKind.EXPOSES)) == 1
    assert len(_rels(graph, RelationKind.CONSUMES)) == 1


def test_module_level_client_falls_back_to_file():
    code = 'fetch("/ping");\n'
    graph, file_id, root, fp = _visit(code)
    _extract_boundaries(
        graph, [(fp, file_id, root, "ts")],
        TYPESCRIPT_DEFAULT_BOUNDARY_EXTRACTORS,
    )
    consumes = _rels(graph, RelationKind.CONSUMES)
    assert len(consumes) == 1
    assert consumes[0].source_id == file_id


def test_nested_function_innermost():
    code = (
        "function outer() {\n"
        "  function inner() {\n"
        '    fetch("/x");\n'
        "  }\n"
        "}\n"
    )
    graph, _file_id, root, fp = _visit(code)
    _extract_boundaries(
        graph, [(fp, _file_id, root, "ts")],
        TYPESCRIPT_DEFAULT_BOUNDARY_EXTRACTORS,
    )
    consumes = _rels(graph, RelationKind.CONSUMES)
    inner_id = make_node_id(
        "p", "app.outer.inner", NodeKind.FUNCTION.value
    )
    assert consumes[0].source_id == inner_id


def test_no_extractors_is_noop():
    graph, file_id, root, fp = _visit('fetch("/x");\n')
    _extract_boundaries(graph, [(fp, file_id, root, "ts")], [])
    assert graph.nodes_by_kind(NodeKind.BOUNDARY) == []


def test_innermost_enclosing_no_candidates():
    assert _innermost_enclosing([], 1, 1) is None


def test_add_boundary_idempotent_node():
    from graphlens import BoundaryRef

    graph = GraphLens()
    graph.add_node(
        Node(id="f", kind=NodeKind.FUNCTION, qualified_name="f", name="f")
    )
    ref = BoundaryRef(
        mechanism="http", role="server", key="GET /x", line=1, col=1
    )
    _add_boundary(graph, "f", ref)
    _add_boundary(graph, "f", ref)
    assert len(graph.nodes_by_kind(NodeKind.BOUNDARY)) == 1
    assert len(_rels(graph, RelationKind.EXPOSES)) == 2
