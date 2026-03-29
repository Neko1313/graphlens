"""Tests for TypeScript project detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphlens_typescript._project_detector import (
    detect_project_name,
    find_typescript_roots,
    is_typescript_project,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestIsTypescriptProject:
    def test_with_package_json(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{}")
        assert is_typescript_project(tmp_path)

    def test_with_tsconfig_json(self, tmp_path: Path):
        (tmp_path / "tsconfig.json").write_text("{}")
        assert is_typescript_project(tmp_path)

    def test_fallback_ts_file(self, tmp_path: Path):
        (tmp_path / "main.ts").write_text("const x = 1;")
        assert is_typescript_project(tmp_path)

    def test_fallback_tsx_file(self, tmp_path: Path):
        (tmp_path / "App.tsx").write_text("export default function App() {}")
        assert is_typescript_project(tmp_path)

    def test_empty_directory(self, tmp_path: Path):
        assert not is_typescript_project(tmp_path)

    def test_non_typescript_project(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'foo'\n")
        assert not is_typescript_project(tmp_path)


class TestFindTypescriptRoots:
    def test_single_project_at_root(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{}")
        assert find_typescript_roots(tmp_path) == [tmp_path]

    def test_monorepo_with_packages(self, tmp_path: Path):
        pkg_a = tmp_path / "packages" / "pkg-a"
        pkg_b = tmp_path / "packages" / "pkg-b"
        pkg_a.mkdir(parents=True)
        pkg_b.mkdir(parents=True)
        (pkg_a / "package.json").write_text('{"name": "pkg-a"}')
        (pkg_b / "package.json").write_text('{"name": "pkg-b"}')
        roots = find_typescript_roots(tmp_path)
        assert pkg_a in roots
        assert pkg_b in roots

    def test_excludes_node_modules(self, tmp_path: Path):
        node_mod = tmp_path / "node_modules" / "some-lib"
        node_mod.mkdir(parents=True)
        (node_mod / "package.json").write_text('{"name": "some-lib"}')
        roots = find_typescript_roots(tmp_path)
        assert node_mod not in roots

    def test_fallback_when_no_markers(self, tmp_path: Path):
        assert find_typescript_roots(tmp_path) == [tmp_path]

    def test_deduplicates_nested_markers(self, tmp_path: Path):
        # If a sub-directory already has a root, deeper marker files
        # inside it should be deduplicated (line 63 continue branch)
        pkg = tmp_path / "packages" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text('{"name": "mypkg"}')
        # A nested marker inside the same package — should not add a duplicate
        nested = pkg / "src"
        nested.mkdir()
        (nested / "tsconfig.json").write_text("{}")
        roots = find_typescript_roots(tmp_path)
        assert pkg in roots
        assert nested not in roots


class TestDetectProjectName:
    def test_from_package_json_name(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "my-cool-app"}')
        assert detect_project_name(tmp_path) == "my_cool_app"

    def test_strips_npm_scope(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "@myorg/my-lib"}')
        assert detect_project_name(tmp_path) == "my_lib"

    def test_fallback_to_dir_name(self, tmp_path: Path):
        assert detect_project_name(tmp_path) == tmp_path.name.lower().replace("-", "_")

    def test_invalid_package_json(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("invalid json")
        # Should fall back to directory name without raising
        assert detect_project_name(tmp_path) == tmp_path.name.lower().replace("-", "_")

    def test_package_json_without_name(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"version": "1.0.0"}')
        assert detect_project_name(tmp_path) == tmp_path.name.lower().replace("-", "_")
