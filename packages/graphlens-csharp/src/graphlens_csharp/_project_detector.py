"""C# project detection: marker files and project name extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphlens.utils import collect_marker_roots

from graphlens_csharp._module_resolver import csproj_properties, first_csproj

if TYPE_CHECKING:
    from pathlib import Path

# A glob marker: every ``.csproj`` is one C# project (one build unit). A
# ``.sln`` merely groups projects, so the csproj — not the solution — is the
# natural sub-root for monorepo discovery.
CSHARP_MARKERS: tuple[str, ...] = ("*.csproj",)

EXCLUDED_DIRS: frozenset[str] = frozenset({
    "bin", "obj", ".vs", ".git", "packages", "node_modules",
    "TestResults", "artifacts",
})


def is_csharp_project(project_root: Path) -> bool:
    """
    Return True if the directory contains a C# project.

    Detection order:
    1. A ``.csproj`` anywhere under ``project_root``.
    2. Fallback: any ``.cs`` file exists under ``project_root``.

    The fallback handles multi-language monorepos and loose C# sources that
    ship no project file.
    """
    def _has(pattern: str) -> bool:
        return any(
            not (EXCLUDED_DIRS & set(p.relative_to(project_root).parts))
            for p in project_root.rglob(pattern)
        )

    if _has("*.csproj"):
        return True
    return _has("*.cs")


def find_csharp_roots(search_root: Path) -> list[Path]:
    """
    Find the actual C# project roots within ``search_root``.

    Walks for ``*.csproj`` markers and returns their parent directories — one
    per distinct project. A marker at ``search_root`` does not hide nested
    marker roots, so a solution that is itself a project and also contains
    project sub-directories yields every root.

    Falls back to ``[search_root]`` when no ``.csproj`` is found anywhere (a
    directory of loose ``.cs`` files with no project file).
    """
    return collect_marker_roots(
        search_root,
        CSHARP_MARKERS,
        excluded_dirs=EXCLUDED_DIRS,
    )


def detect_project_name(project_root: Path) -> str:
    """
    Extract the project name.

    Resolution order:
    1. csproj ``<AssemblyName>``
    2. csproj ``<PackageId>``
    3. csproj file stem (e.g. ``Acme.Billing.csproj`` → ``Acme.Billing``)
    4. project_root directory name
    """
    props = csproj_properties(project_root)
    for key in ("AssemblyName", "PackageId"):
        value = props.get(key)
        if value:
            return value
    csproj = first_csproj(project_root)
    if csproj is not None:
        return csproj.stem
    return project_root.name
