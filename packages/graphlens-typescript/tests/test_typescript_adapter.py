"""Tests for TypescriptAdapter end-to-end."""

from __future__ import annotations

from typing import TYPE_CHECKING

from conftest import nodes_of_kind
from graphlens import NodeKind, RelationKind

from graphlens_typescript import TypescriptAdapter
from graphlens_typescript._deps import DependencyFileParser

if TYPE_CHECKING:
    from pathlib import Path


class TestAdapterMeta:
    def test_language_returns_typescript(self):
        assert TypescriptAdapter().language() == "typescript"

    def test_file_extensions(self):
        exts = TypescriptAdapter().file_extensions()
        assert ".ts" in exts
        assert ".tsx" in exts

    def test_file_extensions_includes_module_ts(self):
        exts = TypescriptAdapter().file_extensions()
        assert ".mts" in exts


class TestCanHandle:
    def test_typescript_project_with_package_json(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "foo"}')
        assert TypescriptAdapter().can_handle(tmp_path)

    def test_typescript_project_with_tsconfig(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text("{}")
        assert TypescriptAdapter().can_handle(tmp_path)

    def test_non_typescript_project(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
        assert not TypescriptAdapter().can_handle(tmp_path)

    def test_empty_directory(self, tmp_path: Path):
        assert not TypescriptAdapter().can_handle(tmp_path)


class TestCollectFiles:
    def test_excludes_declaration_files(self, tmp_path: Path):
        (tmp_path / "index.ts").write_text("export const x = 1;")
        (tmp_path / "types.d.ts").write_text("export type Foo = string;")
        (tmp_path / "package.json").write_text("{}")
        files = TypescriptAdapter().collect_files(tmp_path)
        names = [f.name for f in files]
        assert "index.ts" in names
        assert "types.d.ts" not in names

    def test_collects_tsx_files(self, tmp_path: Path):
        (tmp_path / "App.tsx").write_text("export default function App() {}")
        (tmp_path / "package.json").write_text("{}")
        files = TypescriptAdapter().collect_files(tmp_path)
        assert any(f.name == "App.tsx" for f in files)


class TestAnalyze:
    def test_returns_graphlens(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        assert graph is not None

    def test_has_project_node(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        projects = nodes_of_kind(graph, NodeKind.PROJECT)
        assert len(projects) == 1

    def test_project_name_normalized(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        projects = nodes_of_kind(graph, NodeKind.PROJECT)
        assert projects[0].name == "my_ts_app"

    def test_has_module_nodes(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        modules = nodes_of_kind(graph, NodeKind.MODULE)
        assert len(modules) >= 1

    def test_has_file_nodes(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        files = nodes_of_kind(graph, NodeKind.FILE)
        assert len(files) >= 1

    def test_has_class_nodes(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "MyService" for c in classes)

    def test_has_function_nodes(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        assert any(f.name == "greet" for f in funcs)

    def test_has_contains_relations(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        contains = [r for r in graph.relations if r.kind == RelationKind.CONTAINS]
        assert len(contains) > 0

    def test_has_import_nodes(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        assert len(imports) > 0

    def test_stdlib_imports_classified(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        path_imports = [
            i for i in imports
            if i.metadata.get("origin") == "stdlib"
        ]
        assert len(path_imports) > 0

    def test_third_party_imports_classified(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        tp_imports = [
            i for i in imports
            if i.metadata.get("origin") == "third_party"
        ]
        assert len(tp_imports) > 0

    def test_internal_imports_classified(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        internal_imports = [
            i for i in imports
            if i.metadata.get("origin") == "internal"
        ]
        assert len(internal_imports) > 0

    def test_with_explicit_files(self, sample_typescript_project: Path):
        src = sample_typescript_project / "src" / "myapp"
        files = list(src.glob("*.ts"))
        graph = TypescriptAdapter().analyze(sample_typescript_project, files=files)
        assert len(graph.nodes) > 0

    def test_custom_dep_parsers(self, sample_typescript_project: Path):
        class NoDepsParser(DependencyFileParser):
            def can_parse(self, root: Path) -> bool:
                return False

            def parse(self, root: Path) -> frozenset[str]:
                return frozenset()

        adapter = TypescriptAdapter(dep_parsers=[NoDepsParser()])
        graph = adapter.analyze(sample_typescript_project)
        assert graph is not None

    def test_empty_project(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "empty-proj"}')
        graph = TypescriptAdapter().analyze(tmp_path)
        assert graph is not None

    def test_project_node_contains_modules(self, sample_typescript_project: Path):
        graph = TypescriptAdapter().analyze(sample_typescript_project)
        projects = nodes_of_kind(graph, NodeKind.PROJECT)
        project_id = projects[0].id
        contains_from_project = [
            r for r in graph.relations
            if r.kind == RelationKind.CONTAINS and r.source_id == project_id
        ]
        assert len(contains_from_project) >= 1

    def test_tsx_files_analyzed(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "react-app"}')
        (tmp_path / "App.tsx").write_text(
            "export class AppComponent { render() { return null; } }"
        )
        graph = TypescriptAdapter().analyze(tmp_path)
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "AppComponent" for c in classes)
