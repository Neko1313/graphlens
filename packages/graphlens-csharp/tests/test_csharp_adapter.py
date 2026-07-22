from pathlib import Path

import pytest
from graphlens import (
    RESOLVER_METRICS_KEY,
    RESOLVER_STATUS_KEY,
    AdapterError,
    NodeKind,
    RelationKind,
    ResolverStatus,
)
from graphlens.contracts import Occurrence, ResolvedRef, SymbolResolver

from graphlens_csharp import CsharpAdapter

CSPROJ_ACME = (
    '<Project Sdk="Microsoft.NET.Sdk">'
    "<PropertyGroup>"
    "<RootNamespace>Acme.Billing</RootNamespace>"
    "<AssemblyName>Acme.Billing</AssemblyName>"
    "</PropertyGroup>"
    '<ItemGroup><PackageReference Include="Newtonsoft.Json" Version="13"/>'
    "</ItemGroup></Project>"
)

LIB = """namespace Acme.Billing;

using System;
using Newtonsoft.Json;

public class Repo { public int Find() { return 0; } }

public class Service : Repo {
    private Repo _repo;
    public int Run() {
        this._repo = null;
        return this.Find();
    }
}
"""


class ConstResolver(SymbolResolver):
    """Resolves every position to one fixed ref — exercises the edge pass.

    With ``ref=None`` it stands in for a running-but-empty resolver (structural
    graph only); ``status`` lets a test simulate an unavailable engine.
    """

    def __init__(
        self,
        ref: ResolvedRef | None = None,
        status: ResolverStatus = ResolverStatus.OK,
    ) -> None:
        self._ref = ref
        self._status = status

    def prepare(self, project_root: Path, files: list[Path]) -> None:
        pass

    def definition_at(self, file, line, col):
        return self._ref

    def infer_type_at(self, file, line, col):
        return None

    def references_to(self, file, line, col) -> list[Occurrence]:
        return []

    def status(self) -> ResolverStatus:
        return self._status


def _node(graph, qname, kind):
    return next(
        n
        for n in graph.nodes.values()
        if n.qualified_name == qname and n.kind == kind
    )


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------


def test_adapter_identity():
    adapter = CsharpAdapter()
    assert adapter.language() == "csharp"
    assert adapter.file_extensions() == {".cs"}


def test_can_handle(make_project):
    root = make_project({"P.cs": "class C {}"})
    assert CsharpAdapter().can_handle(root)


def test_cannot_handle(tmp_path):
    (tmp_path / "notes.txt").write_text("nope")
    assert not CsharpAdapter().can_handle(tmp_path)


def test_collect_files_excludes_bin_obj(make_project):
    root = make_project(
        {
            "Good.cs": "class G {}",
            "obj/Gen.cs": "class B1 {}",
            "bin/Out.cs": "class B2 {}",
        }
    )
    names = {f.name for f in CsharpAdapter().collect_files(root)}
    assert "Good.cs" in names
    assert "Gen.cs" not in names
    assert "Out.cs" not in names


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_structural_graph(make_project):
    root = make_project(
        {"Lib.cs": LIB}, csproj=CSPROJ_ACME, csproj_name="Acme.Billing.csproj"
    )
    graph = CsharpAdapter(resolver=ConstResolver()).analyze(root)
    classes = {n.qualified_name for n in graph.nodes.values()
               if n.kind == NodeKind.CLASS}
    assert {"Acme.Billing.Repo", "Acme.Billing.Service"} <= classes
    imports = {
        n.metadata["original_name"]: n.metadata["origin"]
        for n in graph.nodes.values()
        if n.kind == NodeKind.IMPORT
    }
    assert imports["System"] == "stdlib"
    assert imports["Newtonsoft.Json"] == "third_party"


def test_project_contains_top_level_module(make_project):
    root = make_project(
        {"Lib.cs": "namespace Acme.Billing; class C {}"},
        csproj=CSPROJ_ACME,
        csproj_name="Acme.Billing.csproj",
    )
    graph = CsharpAdapter(resolver=ConstResolver()).analyze(root)
    project = _node(graph, "Acme.Billing", NodeKind.PROJECT)
    modules = {
        n.qualified_name: n.id
        for n in graph.nodes.values()
        if n.kind == NodeKind.MODULE
    }
    assert {"Acme", "Acme.Billing"} <= set(modules)
    contains = {
        (r.source_id, r.target_id)
        for r in graph.relations
        if r.kind == RelationKind.CONTAINS
    }
    assert (project.id, modules["Acme"]) in contains


def test_global_namespace_file_under_project(make_project):
    root = make_project({"P.cs": "class C {}"})
    graph = CsharpAdapter(resolver=ConstResolver()).analyze(root)
    project = next(
        n for n in graph.nodes.values() if n.kind == NodeKind.PROJECT
    )
    file_node = next(
        n for n in graph.nodes.values() if n.kind == NodeKind.FILE
    )
    contains = {
        (r.source_id, r.target_id)
        for r in graph.relations
        if r.kind == RelationKind.CONTAINS
    }
    assert (project.id, file_node.id) in contains


# ---------------------------------------------------------------------------
# Resolution pass
# ---------------------------------------------------------------------------


def test_resolution_emits_edges(make_project):
    root = make_project(
        {"Lib.cs": LIB}, csproj=CSPROJ_ACME, csproj_name="Acme.Billing.csproj"
    )
    # First pass to learn the Repo class name-span position.
    probe = CsharpAdapter(resolver=ConstResolver()).analyze(root)
    repo = _node(probe, "Acme.Billing.Repo", NodeKind.CLASS)
    span = repo.metadata["name_span"]
    ref = ResolvedRef(
        full_name="Acme.Billing.Repo",
        file_path=Path(repo.file_path),
        line=span.start_line,
        col=span.start_col,
        kind="",
        origin="internal",
    )
    graph = CsharpAdapter(resolver=ConstResolver(ref)).analyze(root)
    repo_id = _node(graph, "Acme.Billing.Repo", NodeKind.CLASS).id
    kinds = {r.kind for r in graph.relations}
    # base → INHERITS_FROM, annotation → HAS_TYPE, write → REFERENCES,
    # call → CALLS. Every occurrence resolves to Repo here.
    assert RelationKind.CALLS in kinds
    assert RelationKind.INHERITS_FROM in kinds
    assert RelationKind.HAS_TYPE in kinds
    assert RelationKind.REFERENCES in kinds
    calls = [r for r in graph.relations if r.kind == RelationKind.CALLS]
    assert all(r.target_id == repo_id for r in calls)


def test_external_fallback_for_third_party(make_project):
    root = make_project(
        {"Lib.cs": LIB}, csproj=CSPROJ_ACME, csproj_name="Acme.Billing.csproj"
    )
    ref = ResolvedRef(
        full_name="Newtonsoft.Json.JsonConvert",
        file_path=None,
        line=1,
        col=1,
        kind="",
        origin="third_party",
    )
    graph = CsharpAdapter(resolver=ConstResolver(ref)).analyze(root)
    assert any(
        n.kind == NodeKind.EXTERNAL_SYMBOL
        and n.qualified_name == "Newtonsoft.Json.JsonConvert"
        and n.metadata["origin"] == "third_party"
        for n in graph.nodes.values()
    )


def test_internal_span_miss_falls_back_to_external(make_project):
    root = make_project(
        {"Lib.cs": LIB}, csproj=CSPROJ_ACME, csproj_name="Acme.Billing.csproj"
    )
    abs_file = next(root.glob("Lib.cs"))
    ref = ResolvedRef(
        full_name="",
        file_path=abs_file,
        line=9999,
        col=9999,
        kind="",
        origin="internal",
    )
    graph = CsharpAdapter(resolver=ConstResolver(ref)).analyze(root)
    # No node covers (9999, 9999) → external fallback keyed by role@line:col.
    assert any(
        n.kind == NodeKind.EXTERNAL_SYMBOL
        and n.metadata["origin"] == "internal"
        and "@" in n.qualified_name
        for n in graph.nodes.values()
    )


def test_resolver_status_and_metrics_recorded(make_project):
    root = make_project({"P.cs": "class C {}"})
    graph = CsharpAdapter(
        resolver=ConstResolver(status=ResolverStatus.OK)
    ).analyze(root)
    assert graph.metadata[RESOLVER_STATUS_KEY] == "ok"
    assert RESOLVER_METRICS_KEY in graph.metadata


def test_strict_raises_when_degraded(make_project):
    root = make_project({"P.cs": "class C {}"})
    with pytest.raises(AdapterError):
        CsharpAdapter(
            resolver=ConstResolver(status=ResolverStatus.UNAVAILABLE)
        ).analyze(root, strict=True)


# ---------------------------------------------------------------------------
# Monorepo / files override / robustness
# ---------------------------------------------------------------------------


def test_monorepo_two_projects(tmp_path):
    a = tmp_path / "A"
    b = tmp_path / "B"
    a.mkdir()
    b.mkdir()
    (a / "A.csproj").write_text(
        "<Project><PropertyGroup><AssemblyName>Alpha</AssemblyName>"
        "</PropertyGroup></Project>"
    )
    (b / "B.csproj").write_text(
        "<Project><PropertyGroup><AssemblyName>Beta</AssemblyName>"
        "</PropertyGroup></Project>"
    )
    (a / "A.cs").write_text("namespace Alpha; class AC {}")
    (b / "B.cs").write_text("namespace Beta; class BC {}")
    graph = CsharpAdapter(resolver=ConstResolver()).analyze(tmp_path)
    projects = {
        n.qualified_name
        for n in graph.nodes.values()
        if n.kind == NodeKind.PROJECT
    }
    assert projects == {"Alpha", "Beta"}


def test_files_override(make_project):
    root = make_project({"A.cs": "class A {}", "B.cs": "class B {}"})
    graph = CsharpAdapter(resolver=ConstResolver()).analyze(
        root, files=[root / "A.cs"]
    )
    classes = {
        n.qualified_name
        for n in graph.nodes.values()
        if n.kind == NodeKind.CLASS
    }
    assert classes == {"A"}


def test_unreadable_file_skipped(make_project, tmp_path):
    root = make_project({"A.cs": "class A {}"})
    graph = CsharpAdapter(resolver=ConstResolver()).analyze(
        root, files=[root / "A.cs", tmp_path / "ghost.cs"]
    )
    classes = {
        n.qualified_name
        for n in graph.nodes.values()
        if n.kind == NodeKind.CLASS
    }
    assert classes == {"A"}


def test_parse_error_file_still_builds_graph(make_project):
    root = make_project({"Broken.cs": "class C { void M( {"})
    graph = CsharpAdapter(resolver=ConstResolver()).analyze(root)
    assert any(n.kind == NodeKind.PROJECT for n in graph.nodes.values())


def test_duplicate_file_added_once(make_project):
    root = make_project({"A.cs": "class A {}"})
    graph = CsharpAdapter(resolver=ConstResolver()).analyze(
        root, files=[root / "A.cs", root / "A.cs"]
    )
    files = [n for n in graph.nodes.values() if n.kind == NodeKind.FILE]
    assert len(files) == 1


def test_shared_namespace_prefix_reuses_modules(make_project):
    root = make_project(
        {
            "A.cs": "namespace Acme.Billing; class A {}",
            "B.cs": "namespace Acme.Payments; class B {}",
        },
        csproj=CSPROJ_ACME,
        csproj_name="Acme.Billing.csproj",
    )
    graph = CsharpAdapter(resolver=ConstResolver()).analyze(root)
    modules = [
        n.qualified_name
        for n in graph.nodes.values()
        if n.kind == NodeKind.MODULE
    ]
    assert modules.count("Acme") == 1  # shared prefix created once
    assert {"Acme.Billing", "Acme.Payments"} <= set(modules)


def test_two_roots_same_name_share_project(tmp_path):
    same = (
        "<Project><PropertyGroup><AssemblyName>Same</AssemblyName>"
        "</PropertyGroup></Project>"
    )
    for sub in ("A", "B"):
        d = tmp_path / sub
        d.mkdir()
        (d / f"{sub}.csproj").write_text(same)
        (d / f"{sub}.cs").write_text(f"namespace Same; class {sub}Type {{}}")
    graph = CsharpAdapter(resolver=ConstResolver()).analyze(tmp_path)
    projects = [n for n in graph.nodes.values() if n.kind == NodeKind.PROJECT]
    assert len(projects) == 1  # identical AssemblyName → one PROJECT node


def test_default_resolver_degrades_without_server(make_project):
    root = make_project(
        {"P.cs": "namespace App; class C { void M(){ Helper(); } }"}
    )
    graph = CsharpAdapter().analyze(root)
    assert graph.metadata[RESOLVER_STATUS_KEY] == "unavailable"
    assert any(n.kind == NodeKind.CLASS for n in graph.nodes.values())
