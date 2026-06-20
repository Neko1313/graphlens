"""Detect Go projects and their roots (``go.mod`` markers)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphlens.utils.roots import collect_marker_roots

from graphlens_go._deps import read_module_path

if TYPE_CHECKING:
    from pathlib import Path

_GO_MARKERS = ("go.mod",)
_EXCLUDED_DIRS = frozenset({"vendor", ".git", "node_modules", "testdata"})


def is_go_project(root: Path) -> bool:
    """Return True if ``root`` contains a ``go.mod``."""
    return (root / "go.mod").is_file()


def find_go_roots(root: Path) -> list[Path]:
    """
    Return every Go module root at or under ``root`` (monorepo-aware).

    A ``go.mod`` at ``root`` does not hide nested module roots.
    """
    return collect_marker_roots(
        root,
        _GO_MARKERS,
        excluded_dirs=_EXCLUDED_DIRS,
        fallback_to_search_root=False,
    )


def detect_project_name(root: Path) -> str:
    """Return the project name (last segment of the module path)."""
    module_path = read_module_path(root)
    if module_path:
        return module_path.rstrip("/").split("/")[-1]
    return root.name
