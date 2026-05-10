"""Utilities for language project root discovery in monorepos."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable
    from pathlib import Path


def collect_marker_roots(
    search_root: Path,
    markers: Iterable[str],
    *,
    excluded_dirs: Collection[str] = frozenset(),
    marker_filter: Callable[[Path], bool] | None = None,
    fallback_to_search_root: bool = True,
) -> list[Path]:
    """
    Collect all directories containing valid language project markers.

    A marker at ``search_root`` does not hide nested marker roots. This is the
    shared monorepo rule adapters should use when the same language can appear
    in multiple independent packages.
    """
    roots: list[Path] = []
    seen: set[Path] = set()

    for marker in markers:
        for marker_file in sorted(search_root.rglob(marker)):
            if _contains_excluded_dir(marker_file, search_root, excluded_dirs):
                continue
            if marker_filter is not None and not marker_filter(marker_file):
                continue

            root = marker_file.parent
            if root in seen:
                continue

            roots.append(root)
            seen.add(root)

    if roots:
        return sorted(roots)
    return [search_root] if fallback_to_search_root else []


def filter_nested_root_files(
    files: Iterable[Path],
    current_root: Path,
    project_roots: Iterable[Path],
) -> list[Path]:
    """
    Remove files that belong to nested project roots.

    When a monorepo root is itself a project and also contains language
    subprojects, the parent root must not analyze the child roots' files.
    """
    nested_roots = [
        root
        for root in project_roots
        if root != current_root and _is_relative_to(root, current_root)
    ]

    return [
        file
        for file in files
        if not any(_is_relative_to(file, root) for root in nested_roots)
    ]


def _contains_excluded_dir(
    path: Path,
    search_root: Path,
    excluded_dirs: Collection[str],
) -> bool:
    if not excluded_dirs:
        return False
    return bool(set(path.relative_to(search_root).parts) & set(excluded_dirs))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
