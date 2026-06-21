"""Detect Rust crates and their roots (``Cargo.toml`` markers)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphlens.utils.roots import collect_marker_roots

from graphlens_rust._deps import read_crate_name

if TYPE_CHECKING:
    from pathlib import Path

_RUST_MARKERS = ("Cargo.toml",)
_EXCLUDED_DIRS = frozenset({"target", ".git", "node_modules"})


def is_rust_project(root: Path) -> bool:
    """Return True if ``root`` contains a ``Cargo.toml``."""
    return (root / "Cargo.toml").is_file()


def find_rust_roots(root: Path) -> list[Path]:
    """
    Return every crate root at or under ``root`` (workspace-aware).

    A ``Cargo.toml`` at ``root`` does not hide nested crate roots.
    """
    return collect_marker_roots(
        root,
        _RUST_MARKERS,
        excluded_dirs=_EXCLUDED_DIRS,
        fallback_to_search_root=False,
    )


def detect_project_name(root: Path) -> str:
    """Return the crate name, or the directory name as a fallback."""
    name = read_crate_name(root)
    return name if name else root.name
