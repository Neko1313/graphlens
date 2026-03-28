"""Tests for ProjectReader ABC and DiscoveredProject."""

from pathlib import Path

import pytest

from graphlens import DiscoveredProject, ProjectReader


class ConcreteReader(ProjectReader):
    def __init__(self, result: list[DiscoveredProject]) -> None:
        self._result = result

    def discover(self, root: Path) -> list[DiscoveredProject]:
        return self._result


class TestProjectReaderABC:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            ProjectReader()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        reader = ConcreteReader([])
        assert reader is not None

    def test_discover_returns_list(self, tmp_path: Path) -> None:
        reader = ConcreteReader([])
        result = reader.discover(tmp_path)
        assert result == []

    def test_discover_returns_projects(self, tmp_path: Path) -> None:
        projects = [
            DiscoveredProject(root=tmp_path, language="python"),
        ]
        reader = ConcreteReader(projects)
        result = reader.discover(tmp_path)
        assert len(result) == 1
        assert result[0].language == "python"


class TestDiscoveredProject:
    def test_creation(self, tmp_path: Path) -> None:
        dp = DiscoveredProject(root=tmp_path, language="python")
        assert dp.root == tmp_path
        assert dp.language == "python"
        assert dp.files == []

    def test_creation_with_files(self, tmp_path: Path) -> None:
        files = [tmp_path / "a.py", tmp_path / "b.py"]
        dp = DiscoveredProject(root=tmp_path, language="python", files=files)
        assert dp.files == files

    def test_frozen(self, tmp_path: Path) -> None:
        import dataclasses
        dp = DiscoveredProject(root=tmp_path, language="python")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            dp.language = "typescript"  # type: ignore

    def test_equality(self, tmp_path: Path) -> None:
        dp1 = DiscoveredProject(root=tmp_path, language="python")
        dp2 = DiscoveredProject(root=tmp_path, language="python")
        assert dp1 == dp2

    def test_different_language_not_equal(self, tmp_path: Path) -> None:
        dp1 = DiscoveredProject(root=tmp_path, language="python")
        dp2 = DiscoveredProject(root=tmp_path, language="typescript")
        assert dp1 != dp2
