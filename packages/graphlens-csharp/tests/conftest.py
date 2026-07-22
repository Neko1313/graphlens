"""Shared fixtures for the C# adapter test-suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

# A minimal SDK-style csproj used by tests that just need a project marker.
DEFAULT_CSPROJ = (
    '<Project Sdk="Microsoft.NET.Sdk">'
    "<PropertyGroup>"
    "<RootNamespace>App</RootNamespace>"
    "<AssemblyName>App</AssemblyName>"
    "</PropertyGroup>"
    "</Project>"
)


@pytest.fixture
def make_project(tmp_path: Path):
    """Return a helper that writes a csproj + C# files to tmp_path."""

    def _make(
        files: dict[str, str],
        csproj: str | None = DEFAULT_CSPROJ,
        csproj_name: str = "App.csproj",
    ) -> Path:
        if csproj is not None:
            (tmp_path / csproj_name).write_text(csproj, encoding="utf-8")
        for rel, content in files.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return tmp_path

    return _make
