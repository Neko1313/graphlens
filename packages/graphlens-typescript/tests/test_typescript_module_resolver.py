"""Tests for TypeScript module resolver."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from graphlens_typescript._module_resolver import (
    apply_path_alias,
    file_to_qualified_name,
    find_source_roots,
    load_tsconfig_path_aliases,
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
        assert roots == [src, tmp_path]

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


class TestLoadTsconfigPathAliases:
    def test_returns_alias_map_for_at_prefix(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {
                "paths": {"@/*": ["./src/*"]}
            }
        }))
        aliases = load_tsconfig_path_aliases(tmp_path)
        assert aliases == {"@/": "src/"}

    def test_returns_empty_when_no_tsconfig(self, tmp_path: Path):
        aliases = load_tsconfig_path_aliases(tmp_path)
        assert aliases == {}

    def test_returns_empty_when_no_paths_key(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"target": "ES2020"}
        }))
        aliases = load_tsconfig_path_aliases(tmp_path)
        assert aliases == {}

    def test_returns_empty_when_no_compiler_options(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text(json.dumps({"extends": "./base"}))
        aliases = load_tsconfig_path_aliases(tmp_path)
        assert aliases == {}

    def test_tolerates_line_comments(self, tmp_path: Path):
        tsconfig = (
            "{\n"
            '  // This is a comment\n'
            '  "compilerOptions": {\n'
            '    "paths": {"@/*": ["./src/*"]}\n'
            "  }\n"
            "}\n"
        )
        (tmp_path / "tsconfig.json").write_text(tsconfig)
        aliases = load_tsconfig_path_aliases(tmp_path)
        assert aliases == {"@/": "src/"}

    def test_tolerates_trailing_commas(self, tmp_path: Path):
        tsconfig = (
            "{\n"
            '  "compilerOptions": {\n'
            '    "paths": {"@/*": ["./src/*"],},\n'
            "  },\n"
            "}\n"
        )
        (tmp_path / "tsconfig.json").write_text(tsconfig)
        aliases = load_tsconfig_path_aliases(tmp_path)
        assert aliases == {"@/": "src/"}

    def test_ignores_multi_target_patterns(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {
                "paths": {
                    "@/*": ["./src/*", "./fallback/*"],
                    "@utils/*": ["./src/utils/*"],
                }
            }
        }))
        aliases = load_tsconfig_path_aliases(tmp_path)
        # Multi-target ignored, single-target kept
        assert "@/" not in aliases
        assert aliases == {"@utils/": "src/utils/"}

    def test_ignores_patterns_without_glob(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {
                "paths": {
                    "@app": ["./src/app/index"],
                    "@/": ["./src/"],
                }
            }
        }))
        # Neither has the expected /* pattern — both ignored
        aliases = load_tsconfig_path_aliases(tmp_path)
        assert aliases == {}

    def test_returns_empty_on_invalid_json(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text("not valid json }{")
        aliases = load_tsconfig_path_aliases(tmp_path)
        assert aliases == {}

    def test_returns_empty_when_paths_not_a_dict(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"paths": ["@/*"]}
        }))
        aliases = load_tsconfig_path_aliases(tmp_path)
        assert aliases == {}

    def test_ignores_entry_with_non_string_target(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {
                "paths": {"@/*": [123]},
            }
        }))
        aliases = load_tsconfig_path_aliases(tmp_path)
        assert aliases == {}

    def test_multiple_valid_aliases(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {
                "paths": {
                    "@/*": ["./src/*"],
                    "#utils/*": ["./lib/utils/*"],
                }
            }
        }))
        aliases = load_tsconfig_path_aliases(tmp_path)
        assert aliases == {"@/": "src/", "#utils/": "lib/utils/"}


class TestApplyPathAlias:
    def test_rewrites_matching_prefix(self):
        aliases = {"@/": "src/"}
        result = apply_path_alias("@/client/v2", aliases)
        assert result == "src/client/v2"

    def test_returns_unchanged_when_no_match(self):
        aliases = {"@/": "src/"}
        result = apply_path_alias("lodash/merge", aliases)
        assert result == "lodash/merge"

    def test_returns_unchanged_when_aliases_empty(self):
        result = apply_path_alias("@/client/v2", {})
        assert result == "@/client/v2"

    def test_rewrites_first_matching_alias(self):
        aliases = {"@utils/": "src/utils/", "@/": "src/"}
        result = apply_path_alias("@utils/format", aliases)
        assert result == "src/utils/format"

    def test_does_not_partially_match(self):
        aliases = {"@/": "src/"}
        result = apply_path_alias("@stuff/foo", aliases)
        # '@stuff/' does not start with '@/', so '@stuff/' is a prefix of
        # '@stuff/foo' — but '@/' prefix does NOT match '@stuff/foo'
        assert result == "@stuff/foo"


class TestFileToQualifiedNameEdgeCases:
    def test_non_ts_extension_uses_stem(self, tmp_path: Path):
        # A file with a non-TS extension falls to the else branch
        f = tmp_path / "script.js"
        assert file_to_qualified_name(f, tmp_path) == "script"

    def test_src_layout_includes_project_root_for_outside_files(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "src" / "pkg").mkdir(parents=True)
        (tmp_path / "src" / "pkg" / "mod.ts").write_text("")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "mod.test.ts").write_text("")
        files = [
            tmp_path / "src" / "pkg" / "mod.ts",
            tmp_path / "tests" / "mod.test.ts",
        ]
        roots = find_source_roots(tmp_path, files)
        assert roots[0] == tmp_path / "src"
        assert tmp_path in roots
        assert file_to_qualified_name(files[0], roots[0]) == "pkg.mod"
        assert file_to_qualified_name(files[1], tmp_path) == "tests.mod.test"
