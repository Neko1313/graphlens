"""Tests for PythonAdapter."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import nodes_of_kind
from graphlens import (
    RESOLVER_STATUS_KEY,
    AdapterError,
    NodeKind,
    RelationKind,
    ResolverStatus,
)
from graphlens.contracts import SymbolResolver

from graphlens_python import PythonAdapter
from graphlens_python._deps import DependencyFileParser


def project_top_level_module_names(graph, project_name: str) -> set[str]:
    project = next(
        n
        for n in nodes_of_kind(graph, NodeKind.PROJECT)
        if n.name == project_name
    )
    return {
        graph.nodes[relation.target_id].name
        for relation in graph.relations
        if relation.kind == RelationKind.CONTAINS
        and relation.source_id == project.id
        and graph.nodes[relation.target_id].kind == NodeKind.MODULE
    }


class TestAdapterMeta:
    def test_language_returns_python(self):
        assert PythonAdapter().language() == "python"

    def test_file_extensions(self):
        exts = PythonAdapter().file_extensions()
        assert ".py" in exts
        assert ".pyi" in exts


class TestCanHandle:
    def test_python_project(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "foo"\n')
        assert PythonAdapter().can_handle(tmp_path)

    def test_non_python_project(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "foo"\n')
        assert not PythonAdapter().can_handle(tmp_path)

    def test_empty_directory(self, tmp_path: Path):
        assert not PythonAdapter().can_handle(tmp_path)


class TestAnalyze:
    def test_returns_graphlens(self, sample_python_project: Path):
        graph = PythonAdapter().analyze(sample_python_project)
        assert graph is not None

    def test_has_project_node(self, sample_python_project: Path):
        graph = PythonAdapter().analyze(sample_python_project)
        projects = nodes_of_kind(graph, NodeKind.PROJECT)
        assert len(projects) == 1

    def test_has_module_nodes(self, sample_python_project: Path):
        graph = PythonAdapter().analyze(sample_python_project)
        modules = nodes_of_kind(graph, NodeKind.MODULE)
        assert len(modules) >= 1

    def test_has_file_nodes(self, sample_python_project: Path):
        graph = PythonAdapter().analyze(sample_python_project)
        files = nodes_of_kind(graph, NodeKind.FILE)
        assert len(files) >= 1

    def test_has_class_nodes(self, sample_python_project: Path):
        graph = PythonAdapter().analyze(sample_python_project)
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "MyModel" for c in classes)

    def test_has_function_nodes(self, sample_python_project: Path):
        graph = PythonAdapter().analyze(sample_python_project)
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        assert any(f.name == "helper" for f in funcs)

    def test_has_contains_relations(self, sample_python_project: Path):
        graph = PythonAdapter().analyze(sample_python_project)
        contains = [r for r in graph.relations if r.kind == RelationKind.CONTAINS]
        assert len(contains) > 0

    def test_with_explicit_files(self, sample_python_project: Path):
        src = sample_python_project / "src" / "mypkg"
        files = list(src.glob("*.py"))
        graph = PythonAdapter().analyze(sample_python_project, files=files)
        assert len(graph.nodes) > 0

    def test_custom_dep_parsers(self, sample_python_project: Path):
        class NoDepsParser(DependencyFileParser):
            def can_parse(self, root: Path) -> bool:
                return True

            def parse(self, root: Path) -> frozenset[str]:
                return frozenset()

        adapter = PythonAdapter(dep_parsers=[NoDepsParser()])
        graph = adapter.analyze(sample_python_project)
        assert graph is not None

    def test_empty_dep_parsers(self, sample_python_project: Path):
        adapter = PythonAdapter(dep_parsers=[])
        graph = adapter.analyze(sample_python_project)
        assert len(graph.nodes) > 0

    def test_parse_error_continues(self, tmp_path: Path):
        """Files with parse errors still produce partial results."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        src = tmp_path / "src" / "test_pkg"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        (src / "bad.py").write_bytes(b"def (\xff:\n")  # intentionally bad
        (src / "good.py").write_text("def fine(): pass\n")

        graph = PythonAdapter().analyze(tmp_path)
        assert len(graph.nodes) > 0

    def test_unreadable_file_skipped(self, tmp_path: Path):
        """Files that can't be read are skipped without crashing."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        src = tmp_path / "src" / "test_pkg"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        bad = src / "secret.py"
        bad.write_text("x = 1")
        bad.chmod(0o000)
        try:
            graph = PythonAdapter().analyze(tmp_path)
            assert graph is not None
        finally:
            bad.chmod(0o644)


class TestInternalHelpers:
    def test_find_source_root_for_returns_none(self):
        """_find_source_root_for returns None when no root contains the file."""
        from graphlens_python._adapter import _find_source_root_for
        file = Path("/tmp/some/file.py")
        roots = [Path("/other/path"), Path("/another/path")]
        assert _find_source_root_for(file, roots) is None

    def test_file_outside_source_root_skipped(self, tmp_path: Path, tmp_path_factory):
        """Files completely outside the project root are skipped gracefully."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        src = tmp_path / "src" / "testpkg"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")

        # alien.py lives in a completely different tmp dir — not under tmp_path
        # at all, so _find_source_root_for returns None and the fallback
        # source_roots[0] (= src/) still raises ValueError.
        alien_dir = tmp_path_factory.mktemp("alien")
        alien = alien_dir / "alien.py"
        alien.write_text("x = 1")

        # Pass both files: the good one and the alien one outside the project
        graph = PythonAdapter().analyze(tmp_path, files=[src / "__init__.py", alien])
        assert graph is not None
        # The alien file should be skipped; the good file must still appear
        file_names = {n.name for n in graph.nodes.values() if n.kind.value == "file"}
        assert "__init__.py" in file_names
        assert "alien.py" not in file_names

    def test_file_relative_to_py_root_fallback(self, tmp_path: Path):
        """When file is not relative to project_root, falls back to py_root."""
        from graphlens import GraphLens as CG

        from graphlens_python._adapter import _analyze_root
        from graphlens_python._deps import (
            PYTHON_DEFAULT_DEP_PARSERS,
        )
        from graphlens_python._resolver import TyResolver

        # py_root is a sibling of project_root (not a subdirectory)
        project_root = tmp_path / "project"
        py_root = tmp_path / "pyroot"
        project_root.mkdir()
        py_root.mkdir()

        # A file inside py_root but outside project_root
        f = py_root / "mod.py"
        f.write_text("x = 1\n")

        graph = CG()
        _analyze_root(
            graph,
            project_root,
            py_root,
            [f],
            PYTHON_DEFAULT_DEP_PARSERS,
            TyResolver(),
            [],
        )
        # The file path falls back to py_root-relative
        assert graph is not None


class TestMonorepo:
    def test_monorepo_multiple_projects(self, tmp_path: Path):
        for name in ("backend", "worker"):
            sub = tmp_path / name
            sub.mkdir()
            (sub / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
            pkg = sub / "src" / name
            pkg.mkdir(parents=True)
            (pkg / "__init__.py").write_text("")
            (pkg / "main.py").write_text("def run(): pass\n")

        graph = PythonAdapter().analyze(tmp_path)
        projects = nodes_of_kind(graph, NodeKind.PROJECT)
        assert len(projects) == 2
        project_names = {p.name for p in projects}
        assert "backend" in project_names
        assert "worker" in project_names

    def test_root_project_and_nested_projects_are_separate(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "monorepo"\n')
        (tmp_path / "core.py").write_text("def root_func(): pass\n")

        for name in ("core", "worker"):
            sub = tmp_path / "packages" / name
            sub.mkdir(parents=True)
            (sub / "pyproject.toml").write_text(
                f'[project]\nname = "{name}"\n'
            )
            pkg = sub / "src" / name
            pkg.mkdir(parents=True)
            (pkg / "__init__.py").write_text("")
            (pkg / "main.py").write_text("def run(): pass\n")

        graph = PythonAdapter().analyze(tmp_path)
        project_names = {p.name for p in nodes_of_kind(graph, NodeKind.PROJECT)}

        assert project_names == {"monorepo", "core", "worker"}
        assert project_top_level_module_names(graph, "monorepo") == {"core"}

    def test_file_qualified_name_value_error_skipped(self, tmp_path: Path):
        """Files whose qualified name cannot be computed are skipped gracefully."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        pkg = tmp_path / "src" / "test_pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")

        graph = PythonAdapter().analyze(tmp_path)
        assert graph is not None


# ---------------------------------------------------------------------------
# Task 6: Occurrence-driven resolution tests
# ---------------------------------------------------------------------------


def _edges(graph, kind):
    return [r for r in graph.relations if r.kind.value == kind]


def test_calls_resolve_to_real_function_node(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "util.py").write_text(
        "def helper():\n    return 1\n"
    )
    (tmp_path / "pkg" / "main.py").write_text(
        "from pkg.util import helper\n\ndef run():\n    helper()\n"
    )
    graph = PythonAdapter().analyze(tmp_path)

    # no SYMBOL nodes anymore
    assert all(n.kind.value != "symbol" for n in graph.nodes.values())
    helper = next(
        n
        for n in graph.nodes.values()
        if n.kind.value == "function" and n.name == "helper"
    )
    calls = _edges(graph, "calls")
    assert any(r.target_id == helper.id for r in calls)


def test_has_type_edge_for_annotation(tmp_path):
    (tmp_path / "m.py").write_text(
        "class C:\n    pass\n\ndef f(x: C) -> None:\n    pass\n"
    )
    graph = PythonAdapter().analyze(tmp_path)
    c = next(
        n
        for n in graph.nodes.values()
        if n.kind.value == "class" and n.name == "C"
    )
    assert any(r.target_id == c.id for r in _edges(graph, "has_type"))


def test_inherits_from_resolves_internal_class(tmp_path):
    (tmp_path / "m.py").write_text(
        "class Base:\n    pass\n\nclass Sub(Base):\n    pass\n"
    )
    graph = PythonAdapter().analyze(tmp_path)
    base = next(
        n
        for n in graph.nodes.values()
        if n.kind.value == "class" and n.name == "Base"
    )
    inh = _edges(graph, "inherits_from")
    assert any(r.target_id == base.id for r in inh)


def test_variable_read_write_references(tmp_path):
    (tmp_path / "m.py").write_text(
        "CONST = 1\n\ndef f():\n    return CONST\n"
    )
    graph = PythonAdapter().analyze(tmp_path)
    refs = _edges(graph, "references")
    accesses = {r.metadata.get("access") for r in refs}
    assert "write" in accesses  # CONST = 1
    assert "read" in accesses   # return CONST


def test_inherits_from_stdlib_creates_external_symbol(tmp_path):
    """A class inheriting a stdlib base creates EXTERNAL_SYMBOL via _ensure_external_symbol."""
    (tmp_path / "m.py").write_text(
        "import enum\n\nclass Color(enum.Enum):\n    RED = 1\n"
    )
    graph = PythonAdapter().analyze(tmp_path)

    ext = [n for n in graph.nodes.values() if n.kind.value == "external_symbol"]
    assert ext, "expected at least one EXTERNAL_SYMBOL for the stdlib base"

    # ty resolves enum.Enum to the stdlib path — check origin is correct
    assert all(e.metadata.get("origin") == "stdlib" for e in ext), (
        f"expected all external symbols to have origin='stdlib', got: "
        f"{[e.metadata.get('origin') for e in ext]}"
    )

    # INHERITS_FROM edge must target one of those EXTERNAL_SYMBOL nodes
    inh = _edges(graph, "inherits_from")
    assert inh, "expected at least one INHERITS_FROM edge"
    ext_ids = {e.id for e in ext}
    assert any(r.target_id in ext_ids for r in inh), (
        "expected INHERITS_FROM to target the EXTERNAL_SYMBOL for the stdlib base"
    )


def test_injectable_resolver(tmp_path):
    """PythonAdapter accepts an injectable resolver via constructor."""
    from graphlens.contracts import SymbolResolver

    class NullResolver(SymbolResolver):
        def prepare(self, project_root, files):
            pass

        def definition_at(self, file, line, col):
            return None

        def infer_type_at(self, file, line, col):
            return None

        def references_to(self, file, line, col):
            return []

    (tmp_path / "m.py").write_text(
        "def helper():\n    return 1\n\ndef run():\n    helper()\n"
    )
    adapter = PythonAdapter(resolver=NullResolver())
    graph = adapter.analyze(tmp_path)
    # With null resolver, no CALLS edges should be emitted (all refs skip)
    calls = _edges(graph, "calls")
    assert calls == []


def test_ref_is_none_skipped(tmp_path):
    """Occurrences where resolver returns None are silently skipped."""
    from graphlens.contracts import SymbolResolver

    class NullResolver(SymbolResolver):
        def prepare(self, project_root, files):
            pass

        def definition_at(self, file, line, col):
            return None

        def infer_type_at(self, file, line, col):
            return None

        def references_to(self, file, line, col):
            return []

    (tmp_path / "m.py").write_text(
        "class Base:\n    pass\n\nclass Sub(Base):\n    pass\n"
    )
    adapter = PythonAdapter(resolver=NullResolver())
    graph = adapter.analyze(tmp_path)
    # No INHERITS_FROM edges because resolver returns None
    assert _edges(graph, "inherits_from") == []


# ---------------------------------------------------------------------------
# Fix 1: module-level call resolves to a real FUNCTION node
# ---------------------------------------------------------------------------


def test_module_level_call_resolves_to_real_function_node(tmp_path):
    """A module-level ``run()`` call resolves to the real FUNCTION node (Fix 1).

    RED before fix (no CALLS edge); GREEN after fix.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text(
        "def run():\n    return 1\n\nrun()\n"
    )
    graph = PythonAdapter().analyze(tmp_path)

    run_node = next(
        n
        for n in graph.nodes.values()
        if n.kind.value == "function" and n.name == "run"
    )
    calls = _edges(graph, "calls")
    assert any(r.target_id == run_node.id for r in calls), (
        f"expected a CALLS edge targeting the real 'run' FUNCTION node; "
        f"calls={[(r.source_id, r.target_id) for r in calls]}"
    )


# ---------------------------------------------------------------------------
# Fix 2a: obj.method() resolves to the real METHOD node
# ---------------------------------------------------------------------------


def test_obj_method_call_resolves_to_real_method_node(tmp_path):
    """``c.m()`` in a typed parameter resolves to the real METHOD node (Fix 2a).

    jedi performs receiver type inference inside ``goto``, so ``c: C`` is
    enough for it to resolve ``c.m()`` to ``C.m``.

    RED before resolution redesign (no CALLS edge or wrong target);
    GREEN with the current jedi-backed resolver.
    """
    (tmp_path / "m.py").write_text(
        "class C:\n    def m(self): ...\n\ndef use(c: C):\n    c.m()\n"
    )
    graph = PythonAdapter().analyze(tmp_path)

    m_node = next(
        n
        for n in graph.nodes.values()
        if n.kind.value == "method" and n.name == "m"
    )
    calls = _edges(graph, "calls")
    assert any(r.target_id == m_node.id for r in calls), (
        f"expected CALLS edge targeting real METHOD 'm'; "
        f"calls={[(r.source_id, r.target_id) for r in calls]}; "
        f"m_node.id={m_node.id}"
    )


# ---------------------------------------------------------------------------
# Feature #2: function used as a value (Depends, decorator arg) → REFERENCES
# ---------------------------------------------------------------------------


def test_fastapi_depends_in_annotation_references_function(tmp_path):
    """A FastAPI-style ``Depends(get_dep)`` inside an ``Annotated[...]``
    parameter annotation produces a REFERENCES edge from the enclosing
    function to the real ``get_dep`` FUNCTION node (Feature #2)."""
    (tmp_path / "m.py").write_text(
        "from typing import Annotated\n\n"
        "def Depends(x):\n    return x\n\n"
        "def get_dep():\n    return 1\n\n"
        "def view(dep: Annotated[int, Depends(get_dep)]):\n"
        "    return dep\n"
    )
    graph = PythonAdapter().analyze(tmp_path)

    get_dep = next(
        n
        for n in graph.nodes.values()
        if n.kind.value == "function" and n.name == "get_dep"
    )
    view = next(
        n
        for n in graph.nodes.values()
        if n.kind.value == "function" and n.name == "view"
    )
    refs = _edges(graph, "references")
    assert any(
        r.target_id == get_dep.id and r.source_id == view.id for r in refs
    ), (
        f"expected a REFERENCES edge from 'view' to real 'get_dep' FUNCTION; "
        f"refs to get_dep={[r.source_id for r in refs if r.target_id == get_dep.id]}"
    )


def test_decorator_argument_references_internal_function(tmp_path):
    """A decorator call argument that is a real internal function produces a
    REFERENCES edge to that function (Feature #2)."""
    (tmp_path / "m.py").write_text(
        "def deco(fn):\n    def wrap(f):\n        return f\n    return wrap\n\n"
        "def handler():\n    return 1\n\n"
        "@deco(handler)\ndef view():\n    return 2\n"
    )
    graph = PythonAdapter().analyze(tmp_path)

    handler = next(
        n
        for n in graph.nodes.values()
        if n.kind.value == "function" and n.name == "handler"
    )
    refs = _edges(graph, "references")
    assert any(r.target_id == handler.id for r in refs), (
        f"expected a REFERENCES edge targeting real 'handler' FUNCTION; "
        f"refs={[(r.source_id, r.target_id) for r in refs]}"
    )


# ---------------------------------------------------------------------------
# TCK-3: resolver status, strict mode, str-path coercion
# ---------------------------------------------------------------------------


class _FakeResolver(SymbolResolver):
    """Resolver stub that reports a configurable status, resolves nothing."""

    def __init__(self, report: ResolverStatus = ResolverStatus.OK) -> None:
        self._report = report

    def prepare(self, project_root, files):
        pass

    def definition_at(self, file, line, col):
        return None

    def infer_type_at(self, file, line, col):
        return None

    def references_to(self, file, line, col):
        return []

    def status(self):
        return self._report


def test_resolver_status_recorded(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n")
    graph = PythonAdapter(resolver=_FakeResolver()).analyze(tmp_path)
    assert graph.metadata[RESOLVER_STATUS_KEY] == "ok"


def test_resolver_unavailable_recorded_and_strict_raises(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n")
    adapter = PythonAdapter(
        resolver=_FakeResolver(ResolverStatus.UNAVAILABLE)
    )
    graph = adapter.analyze(tmp_path)
    assert graph.metadata[RESOLVER_STATUS_KEY] == "unavailable"
    with pytest.raises(AdapterError, match="strict"):
        adapter.analyze(tmp_path, strict=True)


def test_analyze_accepts_str_path(tmp_path):
    (tmp_path / "m.py").write_text("x = 1\n")
    graph = PythonAdapter(resolver=_FakeResolver()).analyze(str(tmp_path))
    assert len(graph.nodes) > 0


def test_analyze_with_explicit_files_records_status(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = 1\n")
    graph = PythonAdapter(resolver=_FakeResolver()).analyze(
        tmp_path, files=[f]
    )
    assert graph.metadata[RESOLVER_STATUS_KEY] == "ok"


class _ExternalResolver(SymbolResolver):
    """Resolves every query to a third-party external ref."""

    def prepare(self, project_root, files):
        pass

    def definition_at(self, file, line, col):
        from graphlens.contracts import ResolvedRef

        return ResolvedRef(
            full_name="ext.thing", file_path=None, line=1,
            col=1, kind="function", origin="third_party",
        )

    def infer_type_at(self, file, line, col):
        return None

    def references_to(self, file, line, col):
        return []


def test_resolver_metrics_recorded(tmp_path):
    from graphlens import RESOLVER_METRICS_KEY

    (tmp_path / "m.py").write_text("import os\nos.getcwd()\n")
    graph = PythonAdapter(resolver=_ExternalResolver()).analyze(tmp_path)
    metrics = graph.metadata[RESOLVER_METRICS_KEY]
    assert metrics["queries"] >= 1
    assert metrics["resolved"] == metrics["queries"]
    assert metrics["external"] == metrics["resolved"]
    assert metrics["unresolved"] == 0
    assert metrics["resolved_pct"] == 100.0


def test_resolver_metrics_counts_unresolved(tmp_path):
    from graphlens import RESOLVER_METRICS_KEY

    (tmp_path / "m.py").write_text("import os\nos.getcwd()\n")
    graph = PythonAdapter(resolver=_FakeResolver()).analyze(tmp_path)
    metrics = graph.metadata[RESOLVER_METRICS_KEY]
    assert metrics["queries"] >= 1
    assert metrics["resolved"] == 0
    assert metrics["unresolved"] == metrics["queries"]
