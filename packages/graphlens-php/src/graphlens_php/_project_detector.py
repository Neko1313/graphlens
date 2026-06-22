"""PHP project detection: marker files and project name extraction."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from graphlens.utils import collect_marker_roots

if TYPE_CHECKING:
    from pathlib import Path

PHP_MARKERS: tuple[str, ...] = (
    "composer.json",
)

_EXCLUDED_DIRS: frozenset[str] = frozenset({
    "vendor", "node_modules", ".git", "var", "cache",
    "build", "dist", ".phpunit.cache",
})


def is_php_project(project_root: Path) -> bool:
    """
    Return True if the directory contains a PHP project.

    Detection order:
    1. PHP-specific marker files (composer.json)
    2. Fallback: any ``.php`` file exists anywhere under project_root

    The fallback handles multi-language monorepos and plain PHP projects
    that ship no composer manifest.
    """
    if (project_root / "composer.json").exists():
        return True
    return any(project_root.rglob("*.php"))


def find_php_roots(search_root: Path) -> list[Path]:
    """
    Find the actual PHP project roots within search_root.

    Walks for ``composer.json`` markers and returns their parent
    directories — one per distinct PHP sub-project. A marker at
    ``search_root`` does not hide nested marker roots, so a monorepo that is
    itself a project and also contains PHP sub-packages yields every root.

    Falls back to ``[search_root]`` when no markers are found anywhere (a
    directory that contains only bare ``.php`` scripts with no manifest).
    """
    return collect_marker_roots(
        search_root,
        PHP_MARKERS,
        excluded_dirs=_EXCLUDED_DIRS,
    )


def detect_project_name(project_root: Path) -> str:
    """
    Extract the project name.

    Resolution order:
    1. composer.json ``name`` (e.g. ``vendor/package``)
    2. project_root directory name
    """
    composer = project_root / "composer.json"
    if composer.exists():
        try:
            data = json.loads(composer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        name = data.get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and name:
            return name

    return project_root.name
