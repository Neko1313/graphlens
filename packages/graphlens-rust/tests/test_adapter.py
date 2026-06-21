"""Tests for RustAdapter."""

from pathlib import Path

import pytest
from graphlens import (
    RESOLVER_STATUS_KEY,
    AdapterError,
    GraphLens,
    Node,
    NodeKind,
    RelationKind,
)

from graphlens_rust import RustAdapter, RustResolver
from graphlens_rust._adapter import (
    _module_candidates,
    _resolve_internal_imports,
)


def _kinds(graph):
    return {n.kind for n in graph.nodes.values()}


def test_meta():
    adapter = RustAdapter()
    assert adapter.language() == "rust"
    assert ".rs" in adapter.file_extensions()


def test_can_handle(tmp_path: Path):
    assert not RustAdapter().can_handle(tmp_path)
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\n')
    assert RustAdapter().can_handle(tmp_path)


def test_analyze_structural(sample_rust_project: Path):
    graph = RustAdapter().analyze(sample_rust_project)
    for expected in (
        NodeKind.PROJECT,
        NodeKind.MODULE,
        NodeKind.FILE,
        NodeKind.FUNCTION,
        NodeKind.CLASS,
        NodeKind.METHOD,
    ):
        assert expected in _kinds(graph)


def test_status_unavailable(sample_rust_project: Path):
    # Pin the structure-only fallback so the test asserts the degraded path
    # regardless of whether rust-analyzer (the default) is installed here.
    graph = RustAdapter(resolver=RustResolver()).analyze(sample_rust_project)
    assert graph.metadata[RESOLVER_STATUS_KEY] == "unavailable"


def test_accepts_str_path(sample_rust_project: Path):
    graph = RustAdapter().analyze(str(sample_rust_project))
    assert len(graph.nodes) > 0


def test_strict_raises(sample_rust_project: Path):
    with pytest.raises(AdapterError, match="strict"):
        RustAdapter(resolver=RustResolver()).analyze(
            sample_rust_project, strict=True
        )


def test_import_origins(sample_rust_project: Path):
    graph = RustAdapter().analyze(sample_rust_project)
    origins = {
        n.metadata.get("origin")
        for n in graph.nodes.values()
        if n.kind == NodeKind.EXTERNAL_SYMBOL
    }
    assert "stdlib" in origins
    assert "third_party" in origins


def test_internal_import_resolves_to_module(sample_rust_project: Path):
    # `use crate::util::helper` binds RESOLVES_TO the real `demo::util`
    # MODULE node rather than an EXTERNAL_SYMBOL (CLAUDE.md §9).
    graph = RustAdapter().analyze(sample_rust_project)
    imp = next(
        n
        for n in graph.nodes.values()
        if n.kind == NodeKind.IMPORT and n.name == "crate::util::helper"
    )
    targets = [
        graph.nodes[r.target_id]
        for r in graph.outgoing(imp.id, RelationKind.RESOLVES_TO)
    ]
    assert any(
        t.kind == NodeKind.MODULE and t.qualified_name == "demo::util"
        for t in targets
    )


def test_module_candidates_crate_and_crate_name_rooted():
    assert _module_candidates("crate::util::helper", "demo", "demo") == [
        "demo::util::helper",
        "demo::util",
    ]
    assert _module_candidates("demo::util::helper", "demo", "demo") == [
        "demo::util::helper",
        "demo::util",
    ]


def test_module_candidates_self_and_super():
    assert _module_candidates("self::helper", "demo", "demo::util") == [
        "demo::util::helper",
        "demo::util",
    ]
    assert _module_candidates(
        "super::sibling", "demo", "demo::util::helper"
    ) == ["demo::util::sibling", "demo::util"]


def test_module_candidates_edge_cases():
    # `super` past the crate root, and an empty path, yield no candidates.
    assert _module_candidates("super::super::x", "demo", "demo") == []
    assert _module_candidates("", "demo", "demo") == []
    # A single segment has no parent candidate.
    assert _module_candidates("crate", "demo", "demo") == ["demo"]
    # All-`super` path (no trailing item) resolves to the ancestor module.
    assert _module_candidates("super::super", "demo", "demo::a::b") == [
        "demo"
    ]


def test_resolve_internal_imports_falls_back_to_external_symbol():
    # An internal import whose module was not analyzed still gets an edge.
    graph = GraphLens()
    graph.add_node(
        Node(
            id="imp1",
            kind=NodeKind.IMPORT,
            qualified_name="src/m.rs::crate::missing",
            name="crate::missing",
        )
    )
    _resolve_internal_imports(
        graph, "demo", [("imp1", "crate::missing", "demo")], {}
    )
    rels = graph.outgoing("imp1", RelationKind.RESOLVES_TO)
    assert len(rels) == 1
    target = graph.nodes[rels[0].target_id]
    assert target.kind == NodeKind.EXTERNAL_SYMBOL
    assert target.metadata["origin"] == "internal"


def test_explicit_files(sample_rust_project: Path):
    files = [sample_rust_project / "src" / "lib.rs"]
    graph = RustAdapter().analyze(sample_rust_project, files=files)
    assert any(n.kind == NodeKind.FILE for n in graph.nodes.values())


def test_module_qnames(sample_rust_project: Path):
    graph = RustAdapter().analyze(sample_rust_project)
    mods = {
        n.qualified_name
        for n in graph.nodes.values()
        if n.kind == NodeKind.MODULE
    }
    assert "demo" in mods
    assert "demo::util" in mods


def test_monorepo_workspace(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["a", "b"]\n'
    )
    for name in ("a", "b"):
        crate = tmp_path / name
        (crate / "src").mkdir(parents=True)
        (crate / "Cargo.toml").write_text(f'[package]\nname="{name}"\n')
        (crate / "src" / "lib.rs").write_text("pub fn f() {}\n")
    graph = RustAdapter().analyze(tmp_path)
    projects = [
        n for n in graph.nodes.values() if n.kind == NodeKind.PROJECT
    ]
    assert len(projects) >= 2


def test_ensure_file_relative_fallback(tmp_path: Path):
    from graphlens import GraphLens

    from graphlens_rust._adapter import _ensure_file

    project_root = tmp_path / "proj"
    crate_root = tmp_path / "elsewhere"
    project_root.mkdir()
    crate_root.mkdir()
    f = crate_root / "x.rs"
    f.write_text("fn x() {}\n")
    g = GraphLens()
    file_id = _ensure_file(g, "p", project_root, crate_root, f, "mod1")
    assert g.nodes[file_id].file_path == "x.rs"


def test_module_qname_outside_crate(tmp_path: Path):
    from graphlens_rust._adapter import _module_qname

    crate = tmp_path / "crate"
    crate.mkdir()
    other = tmp_path / "other" / "f.rs"
    other.parent.mkdir()
    other.write_text("")
    assert _module_qname(other, crate, "demo") == "demo"


def test_unreadable_file_skipped(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="demo"\n')
    src = tmp_path / "src"
    src.mkdir()
    good = src / "lib.rs"
    good.write_text("pub fn a() {}\n")
    missing = src / "ghost.rs"
    graph = RustAdapter().analyze(tmp_path, files=[good, missing])
    assert any(n.name == "lib.rs" for n in graph.nodes.values())


def test_duplicate_module_deduped(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="demo"\n')
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text("pub fn a() {}\n")
    (src / "main.rs").write_text("fn main() {}\n")
    graph = RustAdapter().analyze(tmp_path)
    demo_mods = [
        n
        for n in graph.nodes.values()
        if n.kind == NodeKind.MODULE and n.qualified_name == "demo"
    ]
    assert len(demo_mods) == 1


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
            qualified_name="m::caller",
            name="caller",
            file_path="a.rs",
            span=Span(1, 1, 3, 1),
            metadata={"name_span": Span(1, 4, 1, 10)},
        )
    )
    g.add_node(
        Node(
            id="callee",
            kind=NodeKind.FUNCTION,
            qualified_name="m::callee",
            name="callee",
            file_path="a.rs",
            span=Span(5, 1, 7, 1),
            metadata={"name_span": Span(5, 4, 5, 10)},
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


def _occ(line=2, col=3, enclosing="caller"):
    from graphlens.utils.span import Span

    from graphlens_rust._visitor import OccurrenceRef

    return (
        "a.rs",
        OccurrenceRef(
            role="call",
            line=line,
            col=col,
            enclosing_id=enclosing,
            span=Span(line, col, line, col + 6),
        ),
    )


def _resolve(graph, resolver, occs, project_root=Path("/nonexistent")):
    from graphlens.utils import SpanIndex

    from graphlens_rust._adapter import _resolve_occurrences

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
    ref = _ref("internal", file_path=Path("a.rs"), line=5, col=4)
    _resolve(g, _FakeResolver(ref), [_occ()])
    calls = _calls(g)
    assert len(calls) == 1
    assert calls[0].source_id == "caller"
    assert calls[0].target_id == "callee"


def test_resolve_internal_hit_with_absolute_path(tmp_path):
    g = _resolution_graph()
    ref = _ref("internal", file_path=tmp_path / "a.rs", line=5, col=4)
    _resolve(g, _FakeResolver(ref), [_occ()], project_root=tmp_path)
    calls = _calls(g)
    assert len(calls) == 1
    assert calls[0].target_id == "callee"


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
    ref = _ref("internal", file_path=Path("a.rs"), line=99, col=1)
    _resolve(g, _FakeResolver(ref), [_occ()])
    ext = [
        n for n in g.nodes.values() if n.kind == NodeKind.EXTERNAL_SYMBOL
    ]
    assert len(ext) == 1
    assert ext[0].metadata["origin"] == "internal"


def test_resolve_full_name_symbol_is_reused():
    g = _resolution_graph()
    ref = _ref("third_party", full_name="ext::Thing")
    _resolve(g, _FakeResolver(ref), [_occ(line=2), _occ(line=3)])
    ext = [
        n for n in g.nodes.values() if n.kind == NodeKind.EXTERNAL_SYMBOL
    ]
    assert len(ext) == 1
    assert ext[0].qualified_name == "ext::Thing"
    assert len(_calls(g)) == 2


@pytest.mark.skipif(
    not __import__("shutil").which("rust-analyzer"),
    reason="rust-analyzer not installed",
)
def test_rust_analyzer_integration_runs_pipeline(tmp_path: Path):
    """End-to-end: RustAnalyzerResolver drives the resolution pass.

    Validates that rust-analyzer starts and the resolution pass runs to
    completion (status OK). Real cross-file resolution depends on
    rust-analyzer's crate indexing, which is not guaranteed in a CI sandbox,
    so the precise edge binding is covered by the fake-resolver and
    LocationLink unit tests rather than asserted against the live engine.
    """
    from graphlens_rust import RustAnalyzerResolver

    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "m"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "util.rs").write_text("pub fn helper() -> i32 { 1 }\n")
    (src / "main.rs").write_text(
        "mod util;\nfn main() {\n    let _ = util::helper();\n}\n"
    )
    graph = RustAdapter(resolver=RustAnalyzerResolver()).analyze(tmp_path)
    assert graph.metadata[RESOLVER_STATUS_KEY] == "ok"
