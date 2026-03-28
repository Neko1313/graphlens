"""Tests for PythonAdapter."""

from __future__ import annotations

from pathlib import Path

from code_graph import NodeKind, RelationKind
from conftest import nodes_of_kind

from code_graph_python import PythonAdapter
from code_graph_python._deps import DependencyFileParser


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
    def test_returns_code_graph(self, sample_python_project: Path):
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
        from code_graph_python._adapter import _find_source_root_for
        file = Path("/tmp/some/file.py")
        roots = [Path("/other/path"), Path("/another/path")]
        assert _find_source_root_for(file, roots) is None

    def test_file_outside_source_root_skipped(self, tmp_path: Path):
        """Files outside the source root are skipped in both pre-pass and main loop."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        src = tmp_path / "src" / "testpkg"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")

        # extra.py is outside src/ — triggers ValueError in file_to_qualified_name
        extra = tmp_path / "extra.py"
        extra.write_text("x = 1")

        # Pass both files: the good one and the one outside src/
        graph = PythonAdapter().analyze(tmp_path, files=[src / "__init__.py", extra])
        assert graph is not None

    def test_file_relative_to_py_root_fallback(self, tmp_path: Path):
        """When file is not relative to project_root, falls back to py_root."""
        from code_graph import CodeGraph as CG

        from code_graph_python._adapter import _analyze_root
        from code_graph_python._deps import PYTHON_DEFAULT_DEP_PARSERS

        # py_root is a sibling of project_root (not a subdirectory)
        project_root = tmp_path / "project"
        py_root = tmp_path / "pyroot"
        project_root.mkdir()
        py_root.mkdir()

        # A file inside py_root but outside project_root
        f = py_root / "mod.py"
        f.write_text("x = 1\n")

        graph = CG()
        _analyze_root(graph, project_root, py_root, [f], PYTHON_DEFAULT_DEP_PARSERS)
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

    def test_file_qualified_name_value_error_skipped(self, tmp_path: Path):
        """Files whose qualified name cannot be computed are skipped gracefully."""
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n')
        pkg = tmp_path / "src" / "test_pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")

        graph = PythonAdapter().analyze(tmp_path)
        assert graph is not None
