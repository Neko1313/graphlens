"""Tests for LanguageAdapter ABC and collect_files."""

from pathlib import Path

import pytest

from graphlens import GraphLens, LanguageAdapter


class ConcreteAdapter(LanguageAdapter):
    def __init__(self, extensions: set[str] | None = None) -> None:
        self._extensions: set[str] = extensions if extensions is not None else set()

    def language(self) -> str:
        return "test"

    def can_handle(self, project_root: Path) -> bool:
        return True

    def analyze(
        self, project_root: Path, files: list[Path] | None = None
    ) -> GraphLens:
        return GraphLens()

    def file_extensions(self) -> set[str]:
        return self._extensions


class MinimalAdapter(LanguageAdapter):
    """Adapter that relies on the base class default file_extensions."""

    def language(self) -> str:
        return "minimal"

    def can_handle(self, project_root: Path) -> bool:
        return False

    def analyze(self, project_root: Path, files: list[Path] | None = None) -> GraphLens:
        return GraphLens()


class TestLanguageAdapterABC:
    def test_cannot_instantiate_without_abstract_methods(self) -> None:
        with pytest.raises(TypeError):
            LanguageAdapter()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        adapter = ConcreteAdapter()
        assert adapter.language() == "test"

    def test_file_extensions_default_empty(self) -> None:
        adapter = ConcreteAdapter()
        assert adapter.file_extensions() == frozenset()

    def test_collect_files_no_extensions_returns_empty(self) -> None:
        adapter = ConcreteAdapter(extensions=set())
        files = adapter.collect_files(Path("."))
        assert files == []

    def test_default_file_extensions_returns_empty_set(self) -> None:
        """Covers the default file_extensions() return set() in base class."""
        adapter = MinimalAdapter()
        assert adapter.file_extensions() == set()


class TestCollectFiles:
    def test_collects_matching_extensions(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("pass")
        (tmp_path / "b.py").write_text("pass")
        (tmp_path / "readme.md").write_text("# doc")

        adapter = ConcreteAdapter(extensions={".py"})
        files = adapter.collect_files(tmp_path)

        assert len(files) == 2
        assert all(f.suffix == ".py" for f in files)

    def test_excludes_venv(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("pass")
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "helper.py").write_text("pass")

        adapter = ConcreteAdapter(extensions={".py"})
        files = adapter.collect_files(tmp_path)

        assert len(files) == 1
        assert files[0].name == "main.py"

    def test_excludes_pycache(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("pass")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "app.cpython-313.pyc").write_bytes(b"")

        adapter = ConcreteAdapter(extensions={".py", ".pyc"})
        files = adapter.collect_files(tmp_path)

        assert all("__pycache__" not in str(f) for f in files)

    def test_excludes_git(self, tmp_path: Path) -> None:
        (tmp_path / "code.py").write_text("pass")
        git = tmp_path / ".git" / "hooks"
        git.mkdir(parents=True)
        (git / "pre-commit.py").write_text("pass")

        adapter = ConcreteAdapter(extensions={".py"})
        files = adapter.collect_files(tmp_path)

        assert len(files) == 1

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        (tmp_path / "index.py").write_text("pass")
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "helper.py").write_text("pass")

        adapter = ConcreteAdapter(extensions={".py"})
        files = adapter.collect_files(tmp_path)

        assert len(files) == 1

    def test_excludes_dist_and_build(self, tmp_path: Path) -> None:
        (tmp_path / "src.py").write_text("pass")
        for excluded_dir in ("dist", "build"):
            d = tmp_path / excluded_dir
            d.mkdir()
            (d / "artifact.py").write_text("pass")

        adapter = ConcreteAdapter(extensions={".py"})
        files = adapter.collect_files(tmp_path)

        assert len(files) == 1

    def test_sorted_output(self, tmp_path: Path) -> None:
        for name in ("z.py", "a.py", "m.py"):
            (tmp_path / name).write_text("pass")

        adapter = ConcreteAdapter(extensions={".py"})
        files = adapter.collect_files(tmp_path)

        assert files == sorted(files)

    def test_nested_directories_included(self, tmp_path: Path) -> None:
        sub = tmp_path / "pkg" / "sub"
        sub.mkdir(parents=True)
        (tmp_path / "a.py").write_text("pass")
        (sub / "b.py").write_text("pass")

        adapter = ConcreteAdapter(extensions={".py"})
        files = adapter.collect_files(tmp_path)

        assert len(files) == 2

    def test_empty_directory(self, tmp_path: Path) -> None:
        adapter = ConcreteAdapter(extensions={".py"})
        files = adapter.collect_files(tmp_path)
        assert files == []
