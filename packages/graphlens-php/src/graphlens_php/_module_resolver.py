"""Namespace resolution and PSR-4 source-root detection for PHP."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def load_psr4_map(project_root: Path) -> dict[str, list[Path]]:
    r"""
    Return the project's PSR-4 namespace → directories map.

    Reads both ``autoload`` and ``autoload-dev`` so that test namespaces are
    treated as internal. Namespace keys are returned without their trailing
    backslash (``"App\\"`` → ``"App"``). Directories are resolved relative to
    ``project_root``. Returns ``{}`` on any error (never raises).
    """
    composer = project_root / "composer.json"
    if not composer.exists():
        return {}
    try:
        data = json.loads(composer.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, list[Path]] = {}
    for section in ("autoload", "autoload-dev"):
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        psr4 = block.get("psr-4")
        if not isinstance(psr4, dict):
            continue
        for prefix, dirs in psr4.items():
            if not isinstance(prefix, str):  # pragma: no cover
                continue
            namespace = prefix.rstrip("\\")
            dir_list = [dirs] if isinstance(dirs, str) else dirs
            if not isinstance(dir_list, list):
                continue
            resolved = [
                (project_root / d) for d in dir_list if isinstance(d, str)
            ]
            out.setdefault(namespace, []).extend(resolved)
    return out


def internal_namespace_tops(project_root: Path) -> set[str]:
    """Return the top-level segment of every PSR-4 namespace prefix."""
    tops: set[str] = set()
    for namespace in load_psr4_map(project_root):
        if namespace:
            tops.add(namespace.split("\\", maxsplit=1)[0])
    return tops


def find_source_roots(
    project_root: Path,
    files: list[Path],  # noqa: ARG001
) -> list[Path]:
    """
    Detect PHP source roots from the PSR-4 autoload directories.

    Every directory referenced by a PSR-4 prefix becomes a source root, with
    ``project_root`` appended last as a catch-all so files outside any
    declared PSR-4 tree are still attributable.
    """
    roots: list[Path] = []
    for dirs in load_psr4_map(project_root).values():
        for directory in dirs:
            if directory.is_dir() and directory not in roots:
                roots.append(directory)
    if project_root not in roots:
        roots.append(project_root)
    return roots


def path_to_namespace(file_path: Path, project_root: Path) -> str:
    r"""
    Map a file path to its PSR-4 namespace (the file's containing namespace).

    This is a *fallback* used only when a file declares no ``namespace``
    statement of its own — the in-source declaration is always authoritative
    when present. For ``psr-4 {"App\\": "src/"}`` the file
    ``src/Service/UserService.php`` maps to namespace ``App\\Service``.

    Returns ``""`` (the global namespace) when no PSR-4 prefix matches.
    """
    psr4 = load_psr4_map(project_root)
    best_namespace = ""
    best_len = -1
    for namespace, dirs in psr4.items():
        for directory in dirs:
            try:
                relative = file_path.parent.relative_to(directory)
            except ValueError:
                continue
            depth = len(directory.parts)
            if depth <= best_len:
                continue
            sub = [p for p in relative.parts if p not in (".", "")]
            best_namespace = "\\".join([namespace, *sub]) if sub else namespace
            best_len = depth
    return best_namespace
