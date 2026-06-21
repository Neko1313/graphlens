"""Tests for GoAdapter."""

from pathlib import Path

import pytest
from graphlens import (
    RESOLVER_STATUS_KEY,
    AdapterError,
    NodeKind,
    RelationKind,
)

from graphlens_go import GoAdapter


def _kinds(graph):
    return {n.kind for n in graph.nodes.values()}


def test_meta():
    adapter = GoAdapter()
    assert adapter.language() == "go"
    assert ".go" in adapter.file_extensions()


def test_can_handle(tmp_path: Path):
    assert not GoAdapter().can_handle(tmp_path)
    (tmp_path / "go.mod").write_text("module x\n")
    assert GoAdapter().can_handle(tmp_path)


def test_analyze_structural(sample_go_project: Path):
    graph = GoAdapter().analyze(sample_go_project)
    kinds = _kinds(graph)
    for expected in (
        NodeKind.PROJECT,
        NodeKind.MODULE,
        NodeKind.FILE,
        NodeKind.FUNCTION,
        NodeKind.METHOD,
        NodeKind.CLASS,
    ):
        assert expected in kinds


def test_analyze_status_unavailable(sample_go_project: Path):
    graph = GoAdapter().analyze(sample_go_project)
    assert graph.metadata[RESOLVER_STATUS_KEY] == "unavailable"


def test_analyze_accepts_str_path(sample_go_project: Path):
    graph = GoAdapter().analyze(str(sample_go_project))
    assert len(graph.nodes) > 0


def test_strict_raises_when_unavailable(sample_go_project: Path):
    with pytest.raises(AdapterError, match="strict"):
        GoAdapter().analyze(sample_go_project, strict=True)


def test_import_origins(sample_go_project: Path):
    graph = GoAdapter().analyze(sample_go_project)
    origins = {
        n.metadata.get("origin")
        for n in graph.nodes.values()
        if n.kind == NodeKind.EXTERNAL_SYMBOL
    }
    assert "stdlib" in origins
    assert "third_party" in origins


def test_internal_import_resolves_to_module(sample_go_project: Path):
    # `example.com/demo/util` binds RESOLVES_TO the real util MODULE node
    # rather than an EXTERNAL_SYMBOL (CLAUDE.md §9).
    graph = GoAdapter().analyze(sample_go_project)
    imp = next(
        n
        for n in graph.nodes.values()
        if n.kind == NodeKind.IMPORT
        and n.name == "example.com/demo/util"
    )
    targets = [
        graph.nodes[r.target_id]
        for r in graph.outgoing(imp.id, RelationKind.RESOLVES_TO)
    ]
    assert any(
        t.kind == NodeKind.MODULE
        and t.qualified_name == "example.com/demo/util"
        for t in targets
    )


def test_package_qname_outside_root_does_not_crash(tmp_path: Path):
    # A file passed explicitly that lives outside the module root must not
    # raise (it falls back to the root package).
    from graphlens_go._adapter import _package_qname

    outside = tmp_path / "elsewhere" / "x.go"
    assert (
        _package_qname(outside, tmp_path / "proj", "example.com/m")
        == "example.com/m"
    )


def test_resolve_internal_imports_falls_back_to_external_symbol():
    # An internal import whose package was not analyzed still gets an edge.
    from graphlens import GraphLens, Node

    from graphlens_go._adapter import _resolve_internal_imports

    graph = GraphLens()
    graph.add_node(
        Node(
            id="imp1",
            kind=NodeKind.IMPORT,
            qualified_name="a.go::example.com/m/missing",
            name="example.com/m/missing",
        )
    )
    _resolve_internal_imports(graph, "m", [("imp1", "example.com/m/missing")], {})
    rels = graph.outgoing("imp1", RelationKind.RESOLVES_TO)
    assert len(rels) == 1
    target = graph.nodes[rels[0].target_id]
    assert target.kind == NodeKind.EXTERNAL_SYMBOL
    assert target.metadata["origin"] == "internal"


def test_explicit_files(sample_go_project: Path):
    files = [sample_go_project / "main.go"]
    graph = GoAdapter().analyze(sample_go_project, files=files)
    assert any(n.kind == NodeKind.FILE for n in graph.nodes.values())


def test_monorepo_multiple_projects(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module root\n")
    (tmp_path / "a.go").write_text("package main\nfunc A() {}\n")
    sub = tmp_path / "svc"
    sub.mkdir()
    (sub / "go.mod").write_text("module root/svc\n")
    (sub / "b.go").write_text("package svc\nfunc B() {}\n")

    graph = GoAdapter().analyze(tmp_path)
    projects = [
        n for n in graph.nodes.values() if n.kind == NodeKind.PROJECT
    ]
    assert len(projects) >= 2


def test_unreadable_file_skipped(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module root\n")
    good = tmp_path / "a.go"
    good.write_text("package main\nfunc A() {}\n")
    missing = tmp_path / "ghost.go"
    graph = GoAdapter().analyze(tmp_path, files=[good, missing])
    assert any(n.name == "a.go" for n in graph.nodes.values())


def test_ensure_file_relative_fallback(tmp_path):
    from graphlens import GraphLens

    from graphlens_go._adapter import _ensure_file

    project_root = tmp_path / "proj"
    go_root = tmp_path / "elsewhere"
    project_root.mkdir()
    go_root.mkdir()
    f = go_root / "x.go"
    f.write_text("package x\n")
    g = GraphLens()
    file_id = _ensure_file(g, "p", project_root, go_root, f, "mod1")
    assert file_id in g.nodes
    assert g.nodes[file_id].file_path == "x.go"


# ---------------------------------------------------------------------------
# Resolution pass (TCK-12) — driven by a fake resolver for determinism
# ---------------------------------------------------------------------------


def _resolution_graph():
    from graphlens import GraphLens, Node
    from graphlens.utils.span import Span

    g = GraphLens()
    g.add_node(
        Node(
            id="caller",
            kind=NodeKind.FUNCTION,
            qualified_name="m.Caller",
            name="Caller",
            file_path="a.go",
            span=Span(1, 1, 3, 1),
            metadata={"name_span": Span(1, 6, 1, 12)},
        )
    )
    g.add_node(
        Node(
            id="callee",
            kind=NodeKind.FUNCTION,
            qualified_name="m.Callee",
            name="Callee",
            file_path="a.go",
            span=Span(5, 1, 7, 1),
            metadata={"name_span": Span(5, 6, 5, 12)},
        )
    )
    return g


class _FakeResolver:
    def __init__(self, ref):
        self._ref = ref

    def prepare(self, project_root, files):
        pass

    def definition_at(self, file, line, col):
        return self._ref

    def infer_type_at(self, file, line, col):
        return None

    def references_to(self, file, line, col):
        return []

    def status(self):
        from graphlens import ResolverStatus

        return ResolverStatus.OK


def _occ(line=2, col=3, enclosing="caller", role="call"):
    from graphlens.utils.span import Span

    from graphlens_go._visitor import OccurrenceRef

    return (
        "a.go",
        OccurrenceRef(
            role=role,
            line=line,
            col=col,
            enclosing_id=enclosing,
            span=Span(line, col, line, col + 6),
        ),
    )


def _resolve(graph, resolver, occs, project_root=Path("/nonexistent")):
    from graphlens.utils import SpanIndex

    from graphlens_go._adapter import _resolve_occurrences

    _resolve_occurrences(
        graph,
        "m",
        project_root,
        resolver,
        SpanIndex.from_graph(graph),
        occs,
    )


def _calls(graph):
    from graphlens import RelationKind

    return [r for r in graph.relations if r.kind == RelationKind.CALLS]


def _ref(origin, *, file_path=None, line=0, col=0, full_name=""):
    from graphlens.contracts import ResolvedRef

    return ResolvedRef(
        full_name=full_name,
        file_path=file_path,
        line=line,
        col=col,
        kind="",
        origin=origin,
    )


def test_resolve_internal_hit_emits_call_edge():
    g = _resolution_graph()
    ref = _ref("internal", file_path=Path("a.go"), line=5, col=6)
    _resolve(g, _FakeResolver(ref), [_occ()])
    calls = _calls(g)
    assert len(calls) == 1
    assert calls[0].source_id == "caller"
    assert calls[0].target_id == "callee"


def test_resolve_internal_hit_with_absolute_path(tmp_path):
    """An absolute resolver path is relativized to the graph's convention."""
    g = _resolution_graph()
    ref = _ref("internal", file_path=tmp_path / "a.go", line=5, col=6)
    _resolve(g, _FakeResolver(ref), [_occ()], project_root=tmp_path)
    calls = _calls(g)
    assert len(calls) == 1
    assert calls[0].target_id == "callee"


def test_resolve_base_emits_inherits_from():
    from graphlens import RelationKind

    g = _resolution_graph()
    ref = _ref("internal", file_path=Path("a.go"), line=5, col=6)
    _resolve(g, _FakeResolver(ref), [_occ(role="base")])
    inh = [
        r for r in g.relations if r.kind == RelationKind.INHERITS_FROM
    ]
    assert len(inh) == 1
    assert inh[0].source_id == "caller"
    assert inh[0].target_id == "callee"


def test_resolve_external_creates_external_symbol():
    g = _resolution_graph()
    _resolve(g, _FakeResolver(_ref("stdlib")), [_occ()])
    ext = [
        n for n in g.nodes.values() if n.kind == NodeKind.EXTERNAL_SYMBOL
    ]
    assert len(ext) == 1
    assert ext[0].metadata["origin"] == "stdlib"
    assert _calls(g)[0].target_id == ext[0].id


def test_resolve_none_ref_emits_no_edge():
    g = _resolution_graph()
    _resolve(g, _FakeResolver(None), [_occ()])
    assert _calls(g) == []


def test_resolve_internal_span_miss_falls_back():
    g = _resolution_graph()
    ref = _ref("internal", file_path=Path("a.go"), line=99, col=1)
    _resolve(g, _FakeResolver(ref), [_occ()])
    ext = [
        n for n in g.nodes.values() if n.kind == NodeKind.EXTERNAL_SYMBOL
    ]
    assert len(ext) == 1
    assert ext[0].metadata["origin"] == "internal"


def test_resolve_full_name_symbol_is_reused():
    g = _resolution_graph()
    ref = _ref("third_party", full_name="ext.Thing")
    _resolve(g, _FakeResolver(ref), [_occ(line=2), _occ(line=3)])
    ext = [
        n for n in g.nodes.values() if n.kind == NodeKind.EXTERNAL_SYMBOL
    ]
    assert len(ext) == 1
    assert ext[0].qualified_name == "ext.Thing"
    assert len(_calls(g)) == 2


@pytest.mark.skipif(
    not __import__("shutil").which("gopls"), reason="gopls not installed"
)
def test_gopls_integration_emits_call_edges(tmp_path: Path):
    """End-to-end: GoplsResolver drives the resolution pass over real gopls.

    Validates that the gopls-backed pipeline runs (status OK) and produces
    CALLS edges. The precise internal-node binding is covered exhaustively by
    the fake-resolver unit tests and the LocationLink handling test, which do
    not depend on gopls' indexing latency or path canonicalisation in CI.
    """
    from graphlens import RelationKind

    from graphlens_go import GoplsResolver

    (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
    (tmp_path / "util.go").write_text(
        "package m\n\nfunc Helper() int { return 1 }\n"
    )
    (tmp_path / "main.go").write_text(
        "package m\n\nfunc Run() int {\n\treturn Helper()\n}\n"
    )
    graph = GoAdapter(resolver=GoplsResolver()).analyze(tmp_path)
    assert graph.metadata[RESOLVER_STATUS_KEY] == "ok"
    calls = [r for r in graph.relations if r.kind == RelationKind.CALLS]
    assert calls  # gopls resolved the Helper() call to a definition node
