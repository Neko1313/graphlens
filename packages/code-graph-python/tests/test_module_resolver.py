"""Tests for Python module resolver."""

from pathlib import Path

import pytest

from code_graph_python._module_resolver import (
    file_to_qualified_name,
    find_source_roots,
    is_package_init,
    resolve_relative_import,
)


class TestFindSourceRoots:
    def test_src_layout_detected(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        f = src / "mypkg" / "mod.py"
        f.parent.mkdir(parents=True)
        f.touch()
        roots = find_source_roots(tmp_path, [f])
        assert roots == [src]

    def test_flat_layout_fallback(self, tmp_path: Path):
        f = tmp_path / "mypkg" / "mod.py"
        f.parent.mkdir(parents=True)
        f.touch()
        roots = find_source_roots(tmp_path, [f])
        assert roots == [tmp_path]

    def test_src_exists_but_no_files_in_it(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        f = tmp_path / "mypkg" / "mod.py"
        f.parent.mkdir(parents=True)
        f.touch()
        roots = find_source_roots(tmp_path, [f])
        assert roots == [tmp_path]

    def test_empty_file_list(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        roots = find_source_roots(tmp_path, [])
        assert roots == [tmp_path]


class TestFileToQualifiedName:
    def test_simple_module(self, tmp_path: Path):
        f = tmp_path / "utils.py"
        assert file_to_qualified_name(f, tmp_path) == "utils"

    def test_nested_module(self, tmp_path: Path):
        f = tmp_path / "pkg" / "sub" / "utils.py"
        assert file_to_qualified_name(f, tmp_path) == "pkg.sub.utils"

    def test_init_py_gives_package_name(self, tmp_path: Path):
        f = tmp_path / "mypkg" / "__init__.py"
        assert file_to_qualified_name(f, tmp_path) == "mypkg"

    def test_pyi_extension(self, tmp_path: Path):
        f = tmp_path / "mypkg" / "types.pyi"
        assert file_to_qualified_name(f, tmp_path) == "mypkg.types"

    def test_nested_init(self, tmp_path: Path):
        f = tmp_path / "pkg" / "sub" / "__init__.py"
        assert file_to_qualified_name(f, tmp_path) == "pkg.sub"

    def test_top_level_init_returns_source_root_name(self, tmp_path: Path):
        # __init__.py directly in source_root → use source_root.name
        f = tmp_path / "__init__.py"
        assert file_to_qualified_name(f, tmp_path) == tmp_path.name

    def test_file_not_relative_raises(self, tmp_path: Path):
        other = tmp_path / "other"
        other.mkdir()
        f = tmp_path / "mod.py"
        with pytest.raises(ValueError):
            file_to_qualified_name(f, other)


class TestIsPackageInit:
    def test_init_py(self, tmp_path: Path):
        assert is_package_init(tmp_path / "__init__.py")

    def test_init_pyi(self, tmp_path: Path):
        assert is_package_init(tmp_path / "__init__.pyi")

    def test_regular_file(self, tmp_path: Path):
        assert not is_package_init(tmp_path / "utils.py")

    def test_module_py(self, tmp_path: Path):
        assert not is_package_init(tmp_path / "main.py")


class TestResolveRelativeImport:
    def test_level_1_with_module(self):
        result = resolve_relative_import("mypkg.sub.mod", level=1, module="utils")
        assert result == "mypkg.sub.utils"

    def test_level_1_no_module(self):
        result = resolve_relative_import("mypkg.sub.mod", level=1, module=None)
        assert result == "mypkg.sub"

    def test_level_2_with_module(self):
        result = resolve_relative_import("mypkg.sub.mod", level=2, module="utils")
        assert result == "mypkg.utils"

    def test_level_2_no_module(self):
        result = resolve_relative_import("mypkg.sub.mod", level=2, module=None)
        assert result == "mypkg"

    def test_level_3_to_root(self):
        result = resolve_relative_import("mypkg.sub.mod", level=3, module=None)
        assert result == ""

    def test_level_1_from_init(self):
        # From mypkg/__init__.py, level=1, no module → mypkg
        result = resolve_relative_import("mypkg", level=1, module=None)
        assert result == ""

    def test_level_1_from_init_with_module(self):
        result = resolve_relative_import("mypkg", level=1, module="utils")
        assert result == "utils"

    def test_module_none_empty_base(self):
        result = resolve_relative_import("mod", level=1, module=None)
        assert result == ""
