"""End-to-end tests for the PHP adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from graphlens import (
    AdapterError,
    NodeKind,
    RelationKind,
    ResolverStatus,
)
from graphlens.contracts import ResolvedRef, SymbolResolver
from graphlens.utils import make_node_id

from graphlens_php import PhpAdapter, PhpResolver

if TYPE_CHECKING:
    from pathlib import Path

COMPOSER = {
    "name": "acme/demo",
    "require": {"php": ">=8.1", "monolog/monolog": "^3"},
    "autoload": {"psr-4": {"App\\": "src/"}},
}

USER_PHP = (
    "<?php\n"
    "namespace App;\n"
    "class User {\n"
    "    public function name(): string { return ''; }\n"
    "}\n"
)

SERVICE_PHP = (
    "<?php\n"
    "namespace App;\n"
    "class Service {\n"
    "    public function greet(): void {\n"
    "        helper();\n"
    "        $this->store = 1;\n"
    "    }\n"
    "}\n"
)


class _PinResolver(SymbolResolver):
    """Resolver that pins every query to a single internal definition."""

    def __init__(self, file_path: Path, line: int, col: int) -> None:
        self._ref = ResolvedRef(
            full_name="App\\User",
            file_path=file_path,
            line=line,
            col=col,
            kind="class",
            origin="internal",
        )

    def prepare(self, project_root: Path, files: list[Path]) -> None:
        pass

    def definition_at(self, file: Path, line: int, col: int) -> ResolvedRef:
        return self._ref

    def infer_type_at(self, file, line, col):
        return None

    def references_to(self, file, line, col):
        return []

    def status(self) -> ResolverStatus:
        return ResolverStatus.OK


class _ExternalResolver(SymbolResolver):
    """Resolver that pins every query to an unresolved third-party symbol."""

    def prepare(self, project_root: Path, files: list[Path]) -> None:
        pass

    def definition_at(self, file: Path, line: int, col: int) -> ResolvedRef:
        # Constant full_name so repeated occurrences reuse one EXTERNAL_SYMBOL.
        return ResolvedRef(
            full_name="Vendor\\Thing",
            file_path=None,
            line=0,
            col=0,
            kind="",
            origin="third_party",
        )

    def infer_type_at(self, file, line, col):
        return None

    def references_to(self, file, line, col):
        return []

    def status(self) -> ResolverStatus:
        return ResolverStatus.OK


# ---------------------------------------------------------------------------
# Adapter metadata
# ---------------------------------------------------------------------------


def test_language_and_extensions():
    adapter = PhpAdapter(resolver=PhpResolver())
    assert adapter.language() == "php"
    assert ".php" in adapter.file_extensions()
    assert ".phtml" in adapter.file_extensions()


def test_can_handle(tmp_path: Path):
    adapter = PhpAdapter(resolver=PhpResolver())
    assert adapter.can_handle(tmp_path) is False
    (tmp_path / "composer.json").write_text("{}")
    assert adapter.can_handle(tmp_path) is True


def test_collect_files_excludes_vendor(tmp_path: Path):
    """vendor/ (and build/cache dirs) must not be indexed as project source."""
    (tmp_path / "composer.json").write_text("{}")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.php").write_text("<?php\n")
    vendor = tmp_path / "vendor" / "symfony" / "console"
    vendor.mkdir(parents=True)
    (vendor / "Application.php").write_text("<?php\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "Generated.php").write_text("<?php\n")

    files = PhpAdapter(resolver=PhpResolver()).collect_files(tmp_path)
    names = {f.name for f in files}
    assert names == {"App.php"}


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_analyze_structure(make_project):
    root = make_project(
        {"src/User.php": USER_PHP, "src/Service.php": SERVICE_PHP},
        composer=COMPOSER,
    )
    graph = PhpAdapter(resolver=PhpResolver()).analyze(root)
    assert graph.metadata["resolver_status"] == "unavailable"
    # PROJECT, MODULE(App), FILE x2, CLASS x2, METHOD x2 ...
    project_id = make_node_id("acme/demo", "acme/demo", NodeKind.PROJECT.value)
    assert project_id in graph.nodes
    app_id = make_node_id("acme/demo", "App", NodeKind.MODULE.value)
    assert app_id in graph.nodes
    # PROJECT --CONTAINS--> App module
    assert any(
        r.source_id == project_id
        and r.target_id == app_id
        and r.kind == RelationKind.CONTAINS
        for r in graph.relations
    )


def test_analyze_with_explicit_files(make_project):
    root = make_project(
        {"src/User.php": USER_PHP}, composer=COMPOSER
    )
    files = [root / "src" / "User.php"]
    graph = PhpAdapter(resolver=PhpResolver()).analyze(root, files=files)
    assert make_node_id("acme/demo", "App\\User", NodeKind.CLASS.value) in (
        graph.nodes
    )


def test_global_namespace_file_contained_by_project(make_project):
    root = make_project(
        {"index.php": "<?php\nclass Bootstrap {}\n"}, composer={"name": "a/b"}
    )
    graph = PhpAdapter(resolver=PhpResolver()).analyze(root)
    project_id = make_node_id("a/b", "a/b", NodeKind.PROJECT.value)
    file_id = make_node_id("a/b", "index.php", NodeKind.FILE.value)
    assert any(
        r.source_id == project_id
        and r.target_id == file_id
        and r.kind == RelationKind.CONTAINS
        for r in graph.relations
    )


def test_nested_module_chain(make_project):
    root = make_project(
        {"src/Sub/Deep.php": "<?php\nnamespace App\\Sub;\nclass Deep {}\n"},
        composer=COMPOSER,
    )
    graph = PhpAdapter(resolver=PhpResolver()).analyze(root)
    app = make_node_id("acme/demo", "App", NodeKind.MODULE.value)
    sub = make_node_id("acme/demo", "App\\Sub", NodeKind.MODULE.value)
    assert app in graph.nodes
    assert sub in graph.nodes
    assert any(
        r.source_id == app and r.target_id == sub
        and r.kind == RelationKind.CONTAINS
        for r in graph.relations
    )


# ---------------------------------------------------------------------------
# Resolution pass
# ---------------------------------------------------------------------------


def test_resolution_emits_internal_edges(make_project):
    root = make_project(
        {"src/User.php": USER_PHP, "src/Service.php": SERVICE_PHP},
        composer=COMPOSER,
    )
    user_file = root / "src" / "User.php"
    # "class User" → "User" at line 3, col 7 (1-based)
    resolver = _PinResolver(user_file, 3, 7)
    graph = PhpAdapter(resolver=resolver).analyze(
        root, files=[user_file, root / "src" / "Service.php"]
    )
    user_id = make_node_id("acme/demo", "App\\User", NodeKind.CLASS.value)
    # helper() call is pinned to User → a CALLS edge targets User
    assert any(
        r.kind == RelationKind.CALLS and r.target_id == user_id
        for r in graph.relations
    )
    # $this->store = 1 → REFERENCES with access "write"
    assert any(
        r.kind == RelationKind.REFERENCES
        and r.metadata.get("access") == "write"
        for r in graph.relations
    )


def test_resolution_external_fallback(make_project):
    root = make_project(
        {"src/Service.php": SERVICE_PHP}, composer=COMPOSER
    )
    graph = PhpAdapter(resolver=_ExternalResolver()).analyze(root)
    ext = [
        n
        for n in graph.nodes.values()
        if n.kind == NodeKind.EXTERNAL_SYMBOL
        and n.metadata.get("origin") == "third_party"
    ]
    assert ext


def test_no_occurrences_short_circuit(make_project):
    root = make_project(
        {"src/Empty.php": "<?php\nnamespace App;\nclass Empty_ {}\n"},
        composer=COMPOSER,
    )
    # No calls/refs → resolution pass returns early; still succeeds.
    graph = PhpAdapter(resolver=_ExternalResolver()).analyze(root)
    assert graph.metadata["resolver_status"] == "ok"


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_parse_error_file_continues(make_project):
    root = make_project(
        {"src/Bad.php": "<?php\nnamespace App;\nclass {\n"},
        composer=COMPOSER,
    )
    graph = PhpAdapter(resolver=PhpResolver()).analyze(root)
    assert make_node_id("acme/demo", "acme/demo", NodeKind.PROJECT.value) in (
        graph.nodes
    )


def test_unreadable_file_is_skipped(make_project):
    root = make_project({"src/User.php": USER_PHP}, composer=COMPOSER)
    missing = root / "src" / "ghost.php"
    graph = PhpAdapter(resolver=PhpResolver()).analyze(
        root, files=[missing, root / "src" / "User.php"]
    )
    assert make_node_id("acme/demo", "App\\User", NodeKind.CLASS.value) in (
        graph.nodes
    )


def test_strict_mode_raises_on_degraded(make_project):
    root = make_project({"src/User.php": USER_PHP}, composer=COMPOSER)
    with pytest.raises(AdapterError):
        PhpAdapter(resolver=PhpResolver()).analyze(root, strict=True)


def test_monorepo_shared_project_name(make_project, tmp_path):
    # Two roots that resolve to the same project name exercise the
    # "project node already exists" guard on the second root.
    make_project(
        {"src/A.php": "<?php\nnamespace App;\nclass A {}\n"},
        composer={"name": "shared", "autoload": {"psr-4": {"App\\": "src/"}}},
    )
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "composer.json").write_text(
        '{"name": "shared", "autoload": {"psr-4": {"Sub\\\\": "src/"}}}'
    )
    (sub / "src").mkdir()
    (sub / "src" / "B.php").write_text("<?php\nnamespace Sub;\nclass B {}\n")
    graph = PhpAdapter(resolver=PhpResolver()).analyze(tmp_path)
    project_id = make_node_id("shared", "shared", NodeKind.PROJECT.value)
    assert project_id in graph.nodes


def test_duplicate_file_in_files_list(make_project):
    root = make_project({"src/User.php": USER_PHP}, composer=COMPOSER)
    f = root / "src" / "User.php"
    graph = PhpAdapter(resolver=PhpResolver()).analyze(root, files=[f, f])
    assert make_node_id("acme/demo", "App\\User", NodeKind.CLASS.value) in (
        graph.nodes
    )


def test_default_resolver_is_phpantom():
    from graphlens_php._resolver import PhpantomResolver

    adapter = PhpAdapter()
    assert isinstance(adapter._resolver, PhpantomResolver)


def test_default_dep_parsers():
    from graphlens_php._deps import PHP_DEFAULT_DEP_PARSERS

    adapter = PhpAdapter()
    assert adapter._dep_parsers is PHP_DEFAULT_DEP_PARSERS
