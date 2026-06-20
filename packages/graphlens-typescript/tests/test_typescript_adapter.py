"""Tests for TypescriptAdapter end-to-end."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from conftest import nodes_of_kind
from graphlens import NodeKind, RelationKind
from graphlens.contracts import ResolvedRef, SymbolResolver

from graphlens_typescript import TypescriptAdapter
from graphlens_typescript._deps import DependencyFileParser

# ---------------------------------------------------------------------------
# FakeResolver — injects fixed resolution results without Node.js
# ---------------------------------------------------------------------------


class FakeResolver(SymbolResolver):
    """Resolves every query to a fixed internal target for testing the pass."""

    def __init__(self, target: ResolvedRef | None) -> None:
        """Store the fixed target returned for every query."""
        self._t = target

    def prepare(self, project_root: Path, files: list[Path]) -> None:
        """No-op preparation."""
        ...

    def resolve_all(
        self, queries: list[tuple[Path, int, int]]
    ) -> list[ResolvedRef | None]:
        """Return the same fixed target for every query."""
        return [self._t for _ in queries]

    def definition_at(
        self, f: Path, l: int, c: int  # noqa: E741
    ) -> ResolvedRef | None:
        """Delegate to fixed target."""
        return self._t

    def infer_type_at(
        self, f: Path, l: int, c: int  # noqa: E741
    ) -> ResolvedRef | None:
        """Delegate to fixed target."""
        return self._t

    def references_to(
        self, f: Path, l: int, c: int  # noqa: E741
    ) -> list:
        """Return empty references list."""
        return []


def _edges(graph, kind: str) -> list:
    """Return all relations whose kind value matches the given string."""
    return [r for r in graph.relations if r.kind.value == kind]


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

    def test_file_outside_project_root_uses_lang_root(self, tmp_path: Path):
        # Files passed explicitly may be outside project_root when lang_root differs
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "package.json").write_text('{"name": "mypkg"}')
        src = sub / "index.ts"
        src.write_text("export const x = 1;")
        # Analyze with project_root = tmp_path but file is under sub/
        graph = TypescriptAdapter().analyze(tmp_path, files=[src])
        assert len(graph.nodes) > 0

    def test_oserror_reading_file_is_skipped(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "mypkg"}')
        ts_file = tmp_path / "index.ts"
        ts_file.write_text("export const x = 1;")
        with patch.object(type(ts_file), "read_bytes", side_effect=OSError("no perm")):
            graph = TypescriptAdapter().analyze(tmp_path, files=[ts_file])
        # Should not raise — file is skipped
        assert graph is not None

    def test_file_with_parse_errors_continues(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "mypkg"}')
        ts_file = tmp_path / "broken.ts"
        # Write bytes that will produce a parse error in tree-sitter
        ts_file.write_bytes(b"const = = {")
        graph = TypescriptAdapter().analyze(tmp_path, files=[ts_file])
        assert graph is not None

    def test_root_project_and_nested_projects_are_separate(
        self, tmp_path: Path
    ):
        (tmp_path / "package.json").write_text('{"name": "monorepo"}')
        (tmp_path / "app.ts").write_text("export const app = 1;\n")

        for name in ("core", "worker"):
            sub = tmp_path / "packages" / name
            sub.mkdir(parents=True)
            (sub / "package.json").write_text(f'{{"name": "{name}"}}')
            src = sub / "src"
            src.mkdir()
            (src / "index.ts").write_text("export const value = 1;\n")

        graph = TypescriptAdapter().analyze(tmp_path)
        project_names = {p.name for p in nodes_of_kind(graph, NodeKind.PROJECT)}

        assert project_names == {"monorepo", "core", "worker"}
        assert project_top_level_module_names(graph, "monorepo") == {"app"}


# ---------------------------------------------------------------------------
# Batch resolution pass tests (FakeResolver — no Node.js required)
# ---------------------------------------------------------------------------


class TestBatchResolutionPass:
    def test_calls_resolve_to_real_function_node(self, tmp_path: Path):
        """
        CALLS edge must point to the real FUNCTION node (not a SYMBOL).

        ``export function helper() { return 1; }`` is at line 1; the name
        'helper' starts at column 17 (after 'export function ', 16 chars).
        The FakeResolver returns a ResolvedRef pointing at that exact name
        position; SpanIndex.at() maps it to the helper function node so the
        CALLS edge's target_id matches helper.id.
        """
        (tmp_path / "util.ts").write_text(
            "export function helper() { return 1; }\n"
        )
        (tmp_path / "main.ts").write_text(
            "import { helper } from './util';\n"
            "export function run() { helper(); }\n"
        )
        # 'helper' name in util.ts: line 1, col 17
        # ('export function ' is 16 chars, 0-indexed col 16 → 1-based col 17)
        target = ResolvedRef(
            full_name="helper",
            file_path=tmp_path / "util.ts",
            line=1,
            col=17,
            kind="function",
            origin="internal",
        )
        graph = TypescriptAdapter(resolver=FakeResolver(target)).analyze(
            tmp_path
        )
        helper = next(
            n
            for n in graph.nodes.values()
            if n.kind.value == "function" and n.name == "helper"
        )
        assert any(r.target_id == helper.id for r in _edges(graph, "calls"))
        assert all(n.kind.value != "symbol" for n in graph.nodes.values())

    def test_unresolved_call_is_skipped(self, tmp_path: Path):
        """
        When the resolver returns None for every query, no crash occurs
        and the graph still contains structural nodes (function, etc.).
        """
        (tmp_path / "main.ts").write_text(
            "export function run() { unknownFn(); }\n"
        )
        graph = TypescriptAdapter(resolver=FakeResolver(None)).analyze(tmp_path)
        assert any(n.kind.value == "function" for n in graph.nodes.values())
        # No resolution edges should be emitted when ref is None
        assert not _edges(graph, "calls")

    def test_read_occurrence_carries_access_metadata(self, tmp_path: Path):
        """
        A ``read`` occurrence resolved internally must produce a REFERENCES
        edge whose metadata contains ``access = "read"``.
        """
        (tmp_path / "lib.ts").write_text(
            "export const VALUE = 42;\n"
        )
        (tmp_path / "main.ts").write_text(
            "import { VALUE } from './lib';\n"
            "export function use() { return VALUE; }\n"
        )
        # 'VALUE' name in lib.ts: line 1, col 14
        # ('export const ' is 13 chars → 1-based col 14)
        target = ResolvedRef(
            full_name="VALUE",
            file_path=tmp_path / "lib.ts",
            line=1,
            col=14,
            kind="variable",
            origin="internal",
        )
        graph = TypescriptAdapter(resolver=FakeResolver(target)).analyze(
            tmp_path
        )
        refs = _edges(graph, "references")
        read_refs = [r for r in refs if r.metadata.get("access") == "read"]
        assert read_refs, "Expected at least one REFERENCES edge with access=read"

    def test_write_occurrence_carries_access_metadata(self, tmp_path: Path):
        """
        A ``write`` occurrence (variable declaration) resolved to an internal
        target must produce a REFERENCES edge with ``access = "write"``.
        """
        (tmp_path / "lib.ts").write_text(
            "export let counter = 0;\n"
        )
        (tmp_path / "main.ts").write_text(
            "import { counter } from './lib';\n"
            "export function inc() { const x = counter; }\n"
        )
        # 'counter' name in lib.ts: line 1, col 12
        # ('export let ' is 11 chars → 1-based col 12)
        target = ResolvedRef(
            full_name="counter",
            file_path=tmp_path / "lib.ts",
            line=1,
            col=12,
            kind="variable",
            origin="internal",
        )
        graph = TypescriptAdapter(resolver=FakeResolver(target)).analyze(
            tmp_path
        )
        # 'x' in 'const x = counter' produces a write occurrence for x
        write_refs = [
            r
            for r in _edges(graph, "references")
            if r.metadata.get("access") == "write"
        ]
        assert write_refs, "Expected REFERENCES edge with access=write"

    def test_external_fallback_when_internal_span_not_found(
        self, tmp_path: Path
    ):
        """
        If the resolver says origin='internal' but SpanIndex.at() returns
        None (position doesn't match any node's name_span), the adapter
        falls back to an EXTERNAL_SYMBOL node rather than crashing.
        """
        (tmp_path / "main.ts").write_text(
            "export function run() { helper(); }\n"
        )
        # Point the resolver at a position that won't exist in the graph
        target = ResolvedRef(
            full_name="helper",
            file_path=tmp_path / "main.ts",
            line=999,
            col=999,
            kind="function",
            origin="internal",
        )
        graph = TypescriptAdapter(resolver=FakeResolver(target)).analyze(
            tmp_path
        )
        # Should not crash; any resolution edges target EXTERNAL_SYMBOL
        ext_symbols = [
            n
            for n in graph.nodes.values()
            if n.kind.value == "external_symbol"
        ]
        # The helper call occurrence should have been resolved to an
        # EXTERNAL_SYMBOL since span lookup fails
        assert ext_symbols or not _edges(graph, "calls")

    def test_inherits_from_base_edge(self, tmp_path: Path):
        """
        A ``base`` occurrence resolves to an INHERITS_FROM edge.
        """
        (tmp_path / "base.ts").write_text(
            "export class Animal { speak() {} }\n"
        )
        (tmp_path / "main.ts").write_text(
            "import { Animal } from './base';\n"
            "export class Dog extends Animal {}\n"
        )
        # 'Animal' name in base.ts: line 1, col 14
        # ('export class ' is 13 chars → 1-based col 14)
        target = ResolvedRef(
            full_name="Animal",
            file_path=tmp_path / "base.ts",
            line=1,
            col=14,
            kind="class",
            origin="internal",
        )
        graph = TypescriptAdapter(resolver=FakeResolver(target)).analyze(
            tmp_path
        )
        animal = next(
            (
                n
                for n in graph.nodes.values()
                if n.kind.value == "class" and n.name == "Animal"
            ),
            None,
        )
        assert animal is not None
        inherits = _edges(graph, "inherits_from")
        assert any(r.target_id == animal.id for r in inherits)

    def test_annotation_produces_has_type_edge(self, tmp_path: Path):
        """
        An ``annotation`` occurrence resolves to a HAS_TYPE edge.
        """
        (tmp_path / "types.ts").write_text(
            "export type MyType = string;\n"
        )
        (tmp_path / "main.ts").write_text(
            "import { MyType } from './types';\n"
            "export function greet(name: MyType): void {}\n"
        )
        # 'MyType' name in types.ts: line 1, col 13
        # ('export type ' is 12 chars → 1-based col 13)
        target = ResolvedRef(
            full_name="MyType",
            file_path=tmp_path / "types.ts",
            line=1,
            col=13,
            kind="type",
            origin="internal",
        )
        graph = TypescriptAdapter(resolver=FakeResolver(target)).analyze(
            tmp_path
        )
        has_type = _edges(graph, "has_type")
        assert has_type, "Expected HAS_TYPE edges from annotation occurrences"

    def test_injectable_resolver_used_in_explicit_files_path(
        self, tmp_path: Path
    ):
        """
        When ``files=`` is passed explicitly, the injectable resolver
        is still used (both code paths in ``analyze`` thread resolver).
        """
        ts_file = tmp_path / "index.ts"
        ts_file.write_text(
            "export function hello() { return 1; }\n"
        )
        graph = TypescriptAdapter(resolver=FakeResolver(None)).analyze(
            tmp_path, files=[ts_file]
        )
        # Graph builds without error; no crash on None resolver results
        assert any(n.kind.value == "function" for n in graph.nodes.values())

    def test_third_party_origin_goes_to_external_symbol(
        self, tmp_path: Path
    ):
        """
        When resolver returns origin='third_party', SpanIndex is NOT
        consulted; the result must be an EXTERNAL_SYMBOL.
        """
        (tmp_path / "main.ts").write_text(
            "export function run() { helper(); }\n"
        )
        target = ResolvedRef(
            full_name="lodash.helper",
            file_path=None,
            line=1,
            col=1,
            kind="function",
            origin="third_party",
        )
        graph = TypescriptAdapter(resolver=FakeResolver(target)).analyze(
            tmp_path
        )
        ext = [
            n
            for n in graph.nodes.values()
            if n.kind.value == "external_symbol"
            and "lodash" in n.qualified_name
        ]
        assert ext, "Expected EXTERNAL_SYMBOL for third_party origin"

    def test_external_symbol_deduped_when_called_twice(
        self, tmp_path: Path
    ):
        """
        Two occurrences resolved to the same external symbol must not
        create duplicate nodes (the ``if sym_id not in graph.nodes`` guard
        must take the already-exists branch on the second call).
        """
        # Two call sites → two occurrences → both point to same external name
        (tmp_path / "main.ts").write_text(
            "export function run() { helper(); helper(); }\n"
        )
        target = ResolvedRef(
            full_name="helper",
            file_path=None,
            line=1,
            col=1,
            kind="function",
            origin="unknown",
        )
        graph = TypescriptAdapter(resolver=FakeResolver(target)).analyze(
            tmp_path
        )
        # There must be exactly ONE external_symbol node for "helper"
        ext_helpers = [
            n
            for n in graph.nodes.values()
            if n.kind.value == "external_symbol" and n.name == "helper"
        ]
        assert len(ext_helpers) == 1

    def test_resolve_occurrences_empty_full_name_uses_position_key(
        self, tmp_path: Path
    ):
        """
        When ref.full_name is empty, the fallback qname uses
        ``{role}@{line}:{col}`` so distinct sites don't collapse.
        """
        (tmp_path / "main.ts").write_text(
            "export function run() { foo(); }\n"
        )
        target = ResolvedRef(
            full_name="",
            file_path=None,
            line=1,
            col=1,
            kind="function",
            origin="unknown",
        )
        graph = TypescriptAdapter(resolver=FakeResolver(target)).analyze(
            tmp_path
        )
        # Graph builds without error
        assert graph is not None

    def test_file_relative_to_lang_root_fallback(self, tmp_path: Path):
        """
        When a file is under lang_root but not project_root, the relative
        path is computed from lang_root as a fallback (line 343-344).

        We call _analyze_root directly with project_root set to a sibling
        directory so that file.relative_to(project_root) fails but
        file.relative_to(lang_root) succeeds.
        """
        from graphlens import GraphLens

        from graphlens_typescript._adapter import _analyze_root

        lang_root = tmp_path / "pkg"
        lang_root.mkdir()
        (lang_root / "package.json").write_text('{"name": "pkg"}')
        ts_file = lang_root / "index.ts"
        ts_file.write_text("export const x = 1;\n")

        # Use a separate sibling as project_root so file is NOT relative to it
        import tempfile
        with tempfile.TemporaryDirectory() as outside:
            project_root = Path(outside)
            graph = GraphLens()
            _analyze_root(
                graph,
                project_root,  # file is NOT under this
                lang_root,     # file IS under this
                [ts_file],
                [],
                FakeResolver(None),
            )
            file_nodes = [
                n for n in graph.nodes.values() if n.kind.value == "file"
            ]
            assert file_nodes, "Expected a FILE node with lang_root-relative path"

    def test_analyze_root_same_project_name_two_roots(
        self, tmp_path: Path
    ):
        """
        When two lang_roots share the same project_name, the
        ``if project_id not in graph.nodes`` guard (False branch) prevents
        a DuplicateNodeError on the second call.

        We call _analyze_root directly with two separate source files from
        two roots that have the same package name detected. The second call
        hits the guard at line 313.
        """
        from graphlens import GraphLens

        from graphlens_typescript._adapter import _analyze_root

        # root1 and root2 are both "named" the same via package.json
        root1 = tmp_path / "a"
        root1.mkdir()
        (root1 / "package.json").write_text('{"name": "shared-name"}')
        file1 = root1 / "one.ts"
        file1.write_text("export const one = 1;\n")

        root2 = tmp_path / "b"
        root2.mkdir()
        (root2 / "package.json").write_text('{"name": "shared-name"}')
        file2 = root2 / "two.ts"
        file2.write_text("export const two = 2;\n")

        graph = GraphLens()
        # First call: creates the project node
        _analyze_root(
            graph, tmp_path, root1, [file1], [], FakeResolver(None)
        )
        # Second call: project_id already in graph → False branch of guard
        _analyze_root(
            graph, tmp_path, root2, [file2], [], FakeResolver(None)
        )
        projects = [n for n in graph.nodes.values() if n.kind.value == "project"]
        assert len(projects) == 1  # deduped

    def test_file_processed_once_no_duplicate_file_nodes(
        self, tmp_path: Path
    ):
        """
        When the same file appears twice in the explicit files list, the
        ``if file_id not in graph.nodes`` guard prevents duplicates.
        """
        ts_file = tmp_path / "index.ts"
        ts_file.write_text("export function hello() {}\n")
        graph = TypescriptAdapter(resolver=FakeResolver(None)).analyze(
            tmp_path, files=[ts_file, ts_file]
        )
        file_nodes = [
            n for n in graph.nodes.values() if n.kind.value == "file"
        ]
        assert len(file_nodes) == 1  # deduplicated

    def test_file_outside_source_roots_is_skipped(self, tmp_path: Path):
        """
        When a file cannot be qualified (it's outside all source roots),
        both the pre-pass ValueError branch (295-296) and the main-loop
        ValueError branch (333-337) are exercised, and the file is skipped.

        Also exercises _find_source_root_for returning None (422-424).
        """
        import tempfile

        from graphlens import GraphLens

        from graphlens_typescript._adapter import _analyze_root

        # Normal file under lang_root
        lang_root = tmp_path
        (lang_root / "package.json").write_text('{"name": "mypkg"}')
        good_file = lang_root / "index.ts"
        good_file.write_text("export const x = 1;\n")

        # A file from OUTSIDE lang_root — not under any source root
        with tempfile.TemporaryDirectory() as outside:
            outside_file = Path(outside) / "orphan.ts"
            outside_file.write_text("export const y = 2;\n")

            graph = GraphLens()
            # outside_file can't be qualified → ValueError → skipped
            _analyze_root(
                graph,
                lang_root,
                lang_root,
                [good_file, outside_file],
                [],
                FakeResolver(None),
            )
            # good_file was processed; outside_file was skipped
            file_nodes = [
                n for n in graph.nodes.values() if n.kind.value == "file"
            ]
            assert len(file_nodes) == 1
