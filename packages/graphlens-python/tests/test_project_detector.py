"""Tests for Python project detector."""

from pathlib import Path

from graphlens_python._project_detector import (
    detect_project_name,
    find_python_roots,
    is_python_project,
)


class TestIsPythonProject:
    def test_with_pyproject_toml(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "foo"\n')
        assert is_python_project(tmp_path)

    def test_pyproject_without_project_section(self, tmp_path: Path):
        # pyproject.toml with no [project] section (e.g. Rust tools config)
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
        assert not is_python_project(tmp_path)

    def test_pyproject_invalid_toml_with_py_files(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[[invalid toml\n", encoding="utf-8")
        (tmp_path / "script.py").write_text("pass")
        assert is_python_project(tmp_path)

    def test_pyproject_invalid_toml_no_py_files(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[[invalid toml\n", encoding="utf-8")
        assert not is_python_project(tmp_path)

    def test_with_setup_py(self, tmp_path: Path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\n")
        assert is_python_project(tmp_path)

    def test_with_setup_cfg(self, tmp_path: Path):
        (tmp_path / "setup.cfg").write_text("[metadata]\nname = foo\n")
        assert is_python_project(tmp_path)

    def test_with_pipfile(self, tmp_path: Path):
        (tmp_path / "Pipfile").write_text("[packages]\nrequests = '*'\n")
        assert is_python_project(tmp_path)

    def test_with_requirements_txt(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("requests\n")
        assert is_python_project(tmp_path)

    def test_fallback_py_files(self, tmp_path: Path):
        sub = tmp_path / "scripts"
        sub.mkdir()
        (sub / "run.py").write_text("pass")
        assert is_python_project(tmp_path)

    def test_non_python_project(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "foo"\n')
        assert not is_python_project(tmp_path)

    def test_empty_directory(self, tmp_path: Path):
        assert not is_python_project(tmp_path)


class TestFindPythonRoots:
    def test_single_root(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "foo"\n')
        roots = find_python_roots(tmp_path)
        assert roots == [tmp_path]

    def test_monorepo_multiple_roots(self, tmp_path: Path):
        for sub in ("backend", "worker"):
            d = tmp_path / sub
            d.mkdir()
            (d / "pyproject.toml").write_text(f'[project]\nname = "{sub}"\n')

        roots = find_python_roots(tmp_path)
        assert len(roots) == 2
        assert any(r.name == "backend" for r in roots)
        assert any(r.name == "worker" for r in roots)

    def test_excludes_venv_dirs(self, tmp_path: Path):
        venv = tmp_path / ".venv" / "lib" / "site-packages" / "pkg"
        venv.mkdir(parents=True)
        (venv / "setup.py").write_text("pass")

        real = tmp_path / "app"
        real.mkdir()
        (real / "pyproject.toml").write_text('[project]\nname = "app"\n')

        roots = find_python_roots(tmp_path)
        assert len(roots) == 1
        assert roots[0] == real

    def test_no_markers_falls_back_to_search_root(self, tmp_path: Path):
        # No markers anywhere — fallback to tmp_path
        roots = find_python_roots(tmp_path)
        assert roots == [tmp_path]

    def test_skip_pyproject_without_project_section(self, tmp_path: Path):
        # pyproject.toml without [project] should not be treated as Python root
        sub = tmp_path / "tooling"
        sub.mkdir()
        (sub / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")

        real = tmp_path / "app"
        real.mkdir()
        (real / "setup.py").write_text("pass")

        roots = find_python_roots(tmp_path)
        assert len(roots) == 1
        assert roots[0] == real

    def test_nested_root_not_duplicated(self, tmp_path: Path):
        # If both parent and child have markers, child is skipped (already covered by parent).
        (tmp_path / "setup.py").write_text("pass")
        sub = tmp_path / "subpkg"
        sub.mkdir()
        (sub / "setup.py").write_text("pass")

        roots = find_python_roots(tmp_path)
        # search_root itself has markers → returns [search_root]
        assert roots == [tmp_path]

    def test_nested_candidate_skipped_when_ancestor_already_found(self, tmp_path: Path):
        """Covers line 72: skip candidate already covered by an ancestor root.

        Use 'abc' as parent dir and 'xyz' as child dir — 'abc/pyproject.toml'
        sorts before 'abc/xyz/pyproject.toml' so parent is found first.
        """
        parent = tmp_path / "abc"
        parent.mkdir()
        (parent / "pyproject.toml").write_text('[project]\nname = "parent"\n')

        child = parent / "xyz"  # 'xyz' sorts after 'pyproject.toml'
        child.mkdir()
        (child / "pyproject.toml").write_text('[project]\nname = "child"\n')

        roots = find_python_roots(tmp_path)
        # child is inside parent (already in roots) → should be skipped
        assert any(r == parent for r in roots)
        assert not any(r == child for r in roots)


class TestDetectProjectName:
    def test_from_pyproject_toml(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "my-project"\n')
        assert detect_project_name(tmp_path) == "my-project"

    def test_from_setup_cfg(self, tmp_path: Path):
        (tmp_path / "setup.cfg").write_text("[metadata]\nname = my-project\n")
        assert detect_project_name(tmp_path) == "my-project"

    def test_fallback_to_directory_name(self, tmp_path: Path):
        name = detect_project_name(tmp_path)
        assert name == tmp_path.name

    def test_pyproject_toml_no_project_name(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
        assert detect_project_name(tmp_path) == tmp_path.name

    def test_pyproject_toml_invalid_toml(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[[invalid toml\n", encoding="utf-8")
        assert detect_project_name(tmp_path) == tmp_path.name

    def test_setup_cfg_invalid(self, tmp_path: Path):
        (tmp_path / "setup.cfg").write_text("[invalid\n", encoding="utf-8")
        assert detect_project_name(tmp_path) == tmp_path.name

    def test_pyproject_takes_precedence_over_setup_cfg(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "from-pyproject"\n')
        (tmp_path / "setup.cfg").write_text("[metadata]\nname = from-setup-cfg\n")
        assert detect_project_name(tmp_path) == "from-pyproject"
