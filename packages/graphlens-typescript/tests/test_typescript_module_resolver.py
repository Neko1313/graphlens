"""Tests for TypeScript module resolver."""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphlens_typescript._module_resolver import (
    file_to_qualified_name,
    find_source_roots,
    resolve_relative_import,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestFindSourceRoots:
    def test_src_layout(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        files = [src / "index.ts", src / "utils.ts"]
        roots = find_source_roots(tmp_path, files)
        assert roots == [src]

    def test_flat_layout(self, tmp_path: Path):
        files = [tmp_path / "index.ts", tmp_path / "utils.ts"]
        roots = find_source_roots(tmp_path, files)
        assert roots == [tmp_path]

    def test_empty_files(self, tmp_path: Path):
        roots = find_source_roots(tmp_path, [])
        assert roots == [tmp_path]

    def test_src_dir_exists_but_no_files_in_it(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        files = [tmp_path / "index.ts"]
        roots = find_source_roots(tmp_path, files)
        assert roots == [tmp_path]


class TestFileToQualifiedName:
    def test_simple_file(self, tmp_path: Path):
        f = tmp_path / "utils.ts"
        assert file_to_qualified_name(f, tmp_path) == "utils"

    def test_nested_file(self, tmp_path: Path):
        f = tmp_path / "mypackage" / "utils.ts"
        assert file_to_qualified_name(f, tmp_path) == "mypackage.utils"

    def test_index_file_becomes_package(self, tmp_path: Path):
        f = tmp_path / "mypackage" / "index.ts"
        assert file_to_qualified_name(f, tmp_path) == "mypackage"

    def test_tsx_extension(self, tmp_path: Path):
        f = tmp_path / "components" / "Button.tsx"
        assert file_to_qualified_name(f, tmp_path) == "components.Button"

    def test_declaration_file(self, tmp_path: Path):
        f = tmp_path / "types.d.ts"
        assert file_to_qualified_name(f, tmp_path) == "types"

    def test_deeply_nested(self, tmp_path: Path):
        f = tmp_path / "a" / "b" / "c.ts"
        assert file_to_qualified_name(f, tmp_path) == "a.b.c"

    def test_top_level_index(self, tmp_path: Path):
        f = tmp_path / "index.ts"
        # Top-level index → use source root name
        result = file_to_qualified_name(f, tmp_path)
        assert result == tmp_path.name


class TestResolveRelativeImport:
    def test_same_directory(self):
        result = resolve_relative_import("myapp.core", "./utils")
        assert result == "myapp.utils"

    def test_parent_directory(self):
        result = resolve_relative_import("myapp.sub.core", "../utils")
        assert result == "myapp.utils"

    def test_current_package(self):
        result = resolve_relative_import("myapp.core", ".")
        assert result == "myapp"

    def test_index_import(self):
        # ./index should map to the current package
        result = resolve_relative_import("myapp.sub.core", "./index")
        assert result == "myapp.sub"

    def test_two_levels_up(self):
        result = resolve_relative_import("a.b.c", "../../utils")
        assert result == "utils"

    def test_nested_path(self):
        result = resolve_relative_import("myapp.core", "./sub/helper")
        assert result == "myapp.sub.helper"

    def test_navigate_above_root_returns_top(self):
        # Going above the root should clamp to the top-level module name
        result = resolve_relative_import("a.b", "../../..")
        assert result == "a"


class TestFileToQualifiedNameEdgeCases:
    def test_non_ts_extension_uses_stem(self, tmp_path: Path):
        # A file with a non-TS extension falls to the else branch
        f = tmp_path / "script.js"
        assert file_to_qualified_name(f, tmp_path) == "script"
