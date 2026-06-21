"""Tests for Python cross-language boundary extraction (TCK-6)."""

from __future__ import annotations

from pathlib import Path

from graphlens import GraphLens, Node, NodeKind, RelationKind
from graphlens.utils.ids import make_node_id

from graphlens_python._adapter import (
    _add_boundary,
    _extract_boundaries,
    _innermost_enclosing,
)
from graphlens_python._boundary import (
    PY_DEFAULT_BOUNDARY_EXTRACTORS,
    HttpClientExtractor,
    HttpServerExtractor,
    TemporalExtractor,
    _normalize_http_path,
    _string_template,
    _text,
)
from graphlens_python._visitor import (
    ImportClassifier,
    PythonASTVisitor,
    VisitorContext,
    parse_python,
)


def _root(code: str):
    return parse_python(code.encode()).root_node


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

    def test_fastapi_route_with_param(self):
        code = '@app.get("/users/{id}")\ndef get_user(id):\n    pass\n'
        refs = self.ex.extract(_root(code))
        assert _keys(refs) == {"GET /users/{}"}
        assert refs[0].role == "server"
        assert refs[0].confidence == 1.0
        assert refs[0].detail["method"] == "GET"

    def test_router_post(self):
        code = '@router.post("/items")\ndef create():\n    pass\n'
        assert _keys(self.ex.extract(_root(code))) == {"POST /items"}

    def test_flask_route_multiple_methods(self):
        code = (
            '@app.route("/x", methods=["GET", "POST"])\n'
            "def view():\n    pass\n"
        )
        assert _keys(self.ex.extract(_root(code))) == {
            "GET /x",
            "POST /x",
        }

    def test_flask_route_default_get(self):
        code = '@app.route("/y")\ndef v():\n    pass\n'
        assert _keys(self.ex.extract(_root(code))) == {"GET /y"}

    def test_flask_route_other_kwarg(self):
        code = (
            '@app.route("/z", strict_slashes=False)\n'
            "def v():\n    pass\n"
        )
        assert _keys(self.ex.extract(_root(code))) == {"GET /z"}

    def test_flask_route_empty_methods_defaults_get(self):
        code = '@app.route("/w", methods=[])\ndef v():\n    pass\n'
        assert _keys(self.ex.extract(_root(code))) == {"GET /w"}

    def test_non_http_decorator_ignored(self):
        code = '@app.middleware("http")\ndef m():\n    pass\n'
        assert self.ex.extract(_root(code)) == []

    def test_decorator_without_string_arg_skipped(self):
        code = "@app.get()\ndef g():\n    pass\n"
        assert self.ex.extract(_root(code)) == []

    def test_path_without_leading_slash_normalized(self):
        code = '@app.get("api/x")\ndef g():\n    pass\n'
        assert _keys(self.ex.extract(_root(code))) == {"GET /api/x"}


# --------------------------------------------------------------------------
# HTTP client extractor
# --------------------------------------------------------------------------


class TestHttpClient:
    ex = HttpClientExtractor()

    def test_mechanism(self):
        assert self.ex.mechanism() == "http"

    def test_requests_relative_path(self):
        code = 'def f():\n    requests.get("/api/users")\n'
        refs = self.ex.extract(_root(code))
        assert _keys(refs) == {"GET /api/users"}
        assert refs[0].role == "client"
        assert refs[0].confidence == 0.9

    def test_httpx_absolute_url_strips_host(self):
        code = 'def f():\n    httpx.post("http://svc/api/y")\n'
        refs = self.ex.extract(_root(code))
        assert _keys(refs) == {"POST /api/y"}
        assert refs[0].confidence == 0.8

    def test_dict_get_is_not_a_boundary(self):
        code = 'def f():\n    d.get("some_key")\n'
        assert self.ex.extract(_root(code)) == []

    def test_non_string_arg_skipped(self):
        code = "def f():\n    requests.get(url)\n"
        assert self.ex.extract(_root(code)) == []

    def test_non_http_method_skipped(self):
        code = "def f():\n    cursor.fetchall()\n"
        assert self.ex.extract(_root(code)) == []


# --------------------------------------------------------------------------
# Temporal extractor
# --------------------------------------------------------------------------


class TestTemporal:
    ex = TemporalExtractor()

    def test_mechanism(self):
        assert self.ex.mechanism() == "temporal"

    def test_activity_defn_bare(self):
        code = "@activity.defn\ndef charge():\n    pass\n"
        refs = self.ex.extract(_root(code))
        assert _keys(refs, "server") == {"charge"}

    def test_activity_defn_named(self):
        code = '@activity.defn(name="ChargeCard")\ndef c():\n    pass\n'
        assert _keys(self.ex.extract(_root(code)), "server") == {
            "ChargeCard"
        }

    def test_activity_defn_named_non_string_falls_back(self):
        code = "@activity.defn(name=NAME)\ndef charge():\n    pass\n"
        assert _keys(self.ex.extract(_root(code)), "server") == {"charge"}

    def test_activity_defn_positional_arg_falls_back(self):
        code = "@activity.defn(foo)\ndef charge():\n    pass\n"
        assert _keys(self.ex.extract(_root(code)), "server") == {"charge"}

    def test_non_defn_bare_decorator_ignored(self):
        code = "@foo.bar\ndef x(self):\n    pass\n"
        assert self.ex.extract(_root(code)) == []

    def test_non_defn_call_decorator_ignored(self):
        code = '@app.get("/x")\ndef x():\n    pass\n'
        assert _keys(self.ex.extract(_root(code)), "server") == set()

    def test_execute_activity_string(self):
        code = 'def wf():\n    workflow.execute_activity("ChargeCard")\n'
        assert _keys(self.ex.extract(_root(code)), "client") == {
            "ChargeCard"
        }

    def test_execute_activity_identifier(self):
        code = "def wf():\n    workflow.execute_activity(charge)\n"
        assert _keys(self.ex.extract(_root(code)), "client") == {"charge"}

    def test_execute_activity_attribute(self):
        code = "def wf():\n    workflow.execute_activity(Acts.charge)\n"
        assert _keys(self.ex.extract(_root(code)), "client") == {"charge"}

    def test_execute_activity_no_positional_skipped(self):
        code = "def wf():\n    workflow.execute_activity()\n"
        assert _keys(self.ex.extract(_root(code)), "client") == set()

    def test_execute_activity_keyword_first_skipped(self):
        code = "def wf():\n    workflow.execute_activity(arg=1)\n"
        assert _keys(self.ex.extract(_root(code)), "client") == set()

    def test_execute_activity_call_arg_skipped(self):
        code = "def wf():\n    workflow.execute_activity(make())\n"
        assert _keys(self.ex.extract(_root(code)), "client") == set()

    def test_non_temporal_method_skipped(self):
        code = 'def f():\n    requests.get("/x")\n'
        assert _keys(self.ex.extract(_root(code)), "client") == set()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class TestHelpers:
    def test_text_none(self):
        assert _text(None) == ""

    def test_string_template_plain(self):
        node = _find(_root('x = "/a/b"'), "string")
        assert _string_template(node) == "/a/b"

    def test_string_template_fstring_interpolation(self):
        node = _find(_root('x = f"/u/{i}"'), "string")
        assert _string_template(node) == "/u/{}"

    def test_string_template_escape(self):
        node = _find(_root('x = "/a\\tb"'), "string")
        assert "a" in _string_template(node)

    def test_string_template_empty(self):
        node = _find(_root('x = ""'), "string")
        assert _string_template(node) == ""

    def test_normalize_strips_scheme_and_host(self):
        assert _normalize_http_path("http://h/api/x") == "/api/x"

    def test_normalize_scheme_without_path(self):
        assert _normalize_http_path("http://host") == "/"

    def test_normalize_adds_leading_slash(self):
        assert _normalize_http_path("api/x") == "/api/x"

    def test_normalize_brace_param(self):
        assert _normalize_http_path("/u/{id}") == "/u/{}"

    def test_normalize_flask_converter(self):
        assert _normalize_http_path("/u/<int:id>") == "/u/{}"

    def test_normalize_colon_param(self):
        assert _normalize_http_path("/u/:id") == "/u/{}"

    def test_normalize_numeric_segment(self):
        assert _normalize_http_path("/users/42/posts") == "/users/{}/posts"

    def test_normalize_strips_query(self):
        assert _normalize_http_path("/x?a=1#z") == "/x"

    def test_normalize_trailing_slash(self):
        assert _normalize_http_path("/users/") == "/users"

    def test_normalize_root_kept(self):
        assert _normalize_http_path("/") == "/"

    def test_normalize_all_slashes(self):
        assert _normalize_http_path("//") == "/"


# --------------------------------------------------------------------------
# Adapter integration (visitor-built graph; no resolver)
# --------------------------------------------------------------------------


def _visit(code: str, file_path: str = "/proj/app.py"):
    graph = GraphLens()
    fp = Path(file_path)
    file_id = make_node_id("p", "app.py", NodeKind.FILE.value)
    graph.add_node(
        Node(
            id=file_id,
            kind=NodeKind.FILE,
            qualified_name="app.py",
            name="app.py",
            file_path=str(fp),
        )
    )
    classifier = ImportClassifier(
        stdlib=frozenset(),
        third_party=frozenset(),
        internal=frozenset(),
    )
    ctx = VisitorContext(
        project_name="p",
        file_path=fp,
        source_root=fp.parent,
        module_qualified_name="app",
    )
    root = parse_python(code.encode())
    PythonASTVisitor(
        ctx, graph, file_id, code.encode(), classifier
    ).visit(root.root_node)
    return graph, file_id, root.root_node, fp


def _rels(graph, kind):
    return [r for r in graph.relations if r.kind == kind]


def test_extract_emits_boundary_with_both_roles():
    code = (
        "import requests\n"
        '@app.get("/users/{id}")\n'
        "def get_user(id):\n"
        '    return requests.get("/users/1")\n'
    )
    graph, _file_id, root, fp = _visit(code)
    _extract_boundaries(
        graph, [(fp, _file_id, root)], PY_DEFAULT_BOUNDARY_EXTRACTORS
    )
    boundaries = graph.nodes_by_kind(NodeKind.BOUNDARY)
    # server route and client call collapse onto one boundary node.
    assert len(boundaries) == 1
    assert boundaries[0].metadata["key"] == "GET /users/{}"
    exposes = _rels(graph, RelationKind.EXPOSES)
    consumes = _rels(graph, RelationKind.CONSUMES)
    assert len(exposes) == 1
    assert len(consumes) == 1
    func_id = make_node_id("p", "app.get_user", NodeKind.FUNCTION.value)
    assert exposes[0].source_id == func_id
    assert consumes[0].source_id == func_id


def test_module_level_client_falls_back_to_file():
    code = 'import requests\nrequests.get("/ping")\n'
    graph, file_id, root, fp = _visit(code)
    _extract_boundaries(
        graph, [(fp, file_id, root)], PY_DEFAULT_BOUNDARY_EXTRACTORS
    )
    consumes = _rels(graph, RelationKind.CONSUMES)
    assert len(consumes) == 1
    assert consumes[0].source_id == file_id


def test_nested_function_resolves_to_innermost():
    code = (
        "def outer():\n"
        "    def inner():\n"
        '        return requests.get("/x")\n'
    )
    graph, _file_id, root, fp = _visit(code)
    _extract_boundaries(
        graph, [(fp, _file_id, root)], PY_DEFAULT_BOUNDARY_EXTRACTORS
    )
    consumes = _rels(graph, RelationKind.CONSUMES)
    inner_id = make_node_id(
        "p", "app.outer.inner", NodeKind.FUNCTION.value
    )
    assert consumes[0].source_id == inner_id


def test_sibling_function_is_not_chosen():
    code = (
        "def a():\n"
        "    pass\n"
        "def b():\n"
        '    requests.get("/x")\n'
    )
    graph, _file_id, root, fp = _visit(code)
    _extract_boundaries(
        graph, [(fp, _file_id, root)], PY_DEFAULT_BOUNDARY_EXTRACTORS
    )
    consumes = _rels(graph, RelationKind.CONSUMES)
    b_id = make_node_id("p", "app.b", NodeKind.FUNCTION.value)
    assert consumes[0].source_id == b_id


def test_no_extractors_is_noop():
    code = '@app.get("/x")\ndef g():\n    pass\n'
    graph, file_id, root, fp = _visit(code)
    _extract_boundaries(graph, [(fp, file_id, root)], [])
    assert graph.nodes_by_kind(NodeKind.BOUNDARY) == []


def test_innermost_enclosing_no_candidates():
    assert _innermost_enclosing([], 1, 1) is None


def test_add_boundary_is_idempotent_for_node():
    from graphlens import BoundaryRef

    graph = GraphLens()
    graph.add_node(
        Node(id="f", kind=NodeKind.FUNCTION, qualified_name="f", name="f")
    )
    ref = BoundaryRef(
        mechanism="queue",
        role="server",
        key="orders",
        line=1,
        col=1,
    )
    _add_boundary(graph, "f", ref)
    _add_boundary(graph, "f", ref)
    assert len(graph.nodes_by_kind(NodeKind.BOUNDARY)) == 1
    assert len(_rels(graph, RelationKind.EXPOSES)) == 2
