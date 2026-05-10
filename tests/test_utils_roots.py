"""Tests for monorepo root utilities."""

from pathlib import Path

from graphlens.utils.roots import (
    collect_marker_roots,
    filter_nested_root_files,
)


class TestCollectMarkerRoots:
    def test_includes_root_and_nested_marker_roots(self, tmp_path: Path):
        (tmp_path / "project.toml").write_text("")
        nested = tmp_path / "packages" / "core"
        nested.mkdir(parents=True)
        (nested / "project.toml").write_text("")

        roots = collect_marker_roots(tmp_path, ("project.toml",))

        assert tmp_path in roots
        assert nested in roots

    def test_excludes_ignored_directories(self, tmp_path: Path):
        ignored = tmp_path / ".venv" / "pkg"
        ignored.mkdir(parents=True)
        (ignored / "project.toml").write_text("")

        assert collect_marker_roots(
            tmp_path,
            ("project.toml",),
            excluded_dirs={".venv"},
        ) == [tmp_path]

    def test_marker_filter_skips_invalid_markers(self, tmp_path: Path):
        nested = tmp_path / "packages" / "invalid"
        nested.mkdir(parents=True)
        marker = nested / "project.toml"
        marker.write_text("")

        roots = collect_marker_roots(
            tmp_path,
            ("project.toml",),
            marker_filter=lambda path: path != marker,
        )

        assert roots == [tmp_path]
        assert nested not in roots

    def test_deduplicates_multiple_markers_in_same_root(self, tmp_path: Path):
        (tmp_path / "project.toml").write_text("")
        (tmp_path / "package.json").write_text("{}")

        roots = collect_marker_roots(
            tmp_path,
            ("project.toml", "package.json"),
        )

        assert roots == [tmp_path]

    def test_fallback_can_be_disabled(self, tmp_path: Path):
        assert collect_marker_roots(
            tmp_path,
            ("project.toml",),
            fallback_to_search_root=False,
        ) == []


class TestFilterNestedRootFiles:
    def test_excludes_files_from_nested_project_roots(self, tmp_path: Path):
        nested = tmp_path / "packages" / "core"
        nested.mkdir(parents=True)
        root_file = tmp_path / "core.py"
        nested_file = nested / "main.py"
        root_file.write_text("")
        nested_file.write_text("")

        files = filter_nested_root_files(
            [root_file, nested_file],
            tmp_path,
            [tmp_path, nested],
        )

        assert files == [root_file]

    def test_preserves_files_for_current_nested_root(self, tmp_path: Path):
        nested = tmp_path / "packages" / "core"
        nested.mkdir(parents=True)
        nested_file = nested / "main.py"
        nested_file.write_text("")

        files = filter_nested_root_files(
            [nested_file],
            nested,
            [tmp_path, nested],
        )

        assert files == [nested_file]
