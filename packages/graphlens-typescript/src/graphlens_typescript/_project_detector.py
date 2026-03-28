"""TypeScript project detection: marker files and project name extraction."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

TYPESCRIPT_MARKERS: tuple[str, ...] = (
    "package.json",
    "tsconfig.json",
)

_EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".venv", "venv", "__pycache__", ".git",
    "dist", "build", ".eggs", "node_modules",
    "out", "coverage", ".next", ".nuxt",
})

_NAME_NORMALIZE_RE = re.compile(r"[^a-z0-9_]")


def is_typescript_project(project_root: Path) -> bool:
    """
    Return True if the directory looks like a TypeScript project.

    Detection order:
    1. TypeScript-specific marker files (package.json, tsconfig.json)
    2. Fallback: any .ts or .tsx file exists anywhere under project_root
    """
    if _has_typescript_markers(project_root):
        return True
    return any(
        project_root.rglob("*.ts")
    ) or any(project_root.rglob("*.tsx"))


def find_typescript_roots(search_root: Path) -> list[Path]:
    """
    Find TypeScript project roots within search_root (monorepo support).

    Returns [search_root] if search_root itself has markers.
    Otherwise walks subdirectories for marker files and returns distinct roots.
    Falls back to [search_root] if nothing found.
    """
    if _has_typescript_markers(search_root):
        return [search_root]

    roots: list[Path] = []
    for marker in TYPESCRIPT_MARKERS:
        for marker_file in sorted(search_root.rglob(marker)):
            rel_parts = marker_file.relative_to(search_root).parts
            if _EXCLUDED_DIRS & set(rel_parts):
                continue
            candidate = marker_file.parent
            if any(
                candidate == r or candidate.is_relative_to(r)
                for r in roots
            ):
                continue
            roots.append(candidate)

    return sorted(roots) if roots else [search_root]


def detect_project_name(project_root: Path) -> str:
    """
    Extract the project name from manifest or fall back to directory name.

    Resolution order:
    1. package.json "name" field (hyphens → underscores, lowercased)
    2. project_root directory name
    """
    package_json = project_root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            raw = data.get("name", "")
            if raw:
                # Strip npm scope (e.g. "@scope/pkg" → "pkg")
                if raw.startswith("@") and "/" in raw:
                    raw = raw.split("/", 1)[1]
                # Normalize: lowercase, non-alnum → underscore
                name = _NAME_NORMALIZE_RE.sub("_", raw.lower()).strip("_")
                if name:
                    return name
        except (
            json.JSONDecodeError,
            OSError,
            KeyError,
            TypeError,
            AttributeError,
        ):
            pass
    return _normalize_name(project_root.name)


def _normalize_name(name: str) -> str:
    """Normalize a directory name to a valid Python identifier."""
    return _NAME_NORMALIZE_RE.sub("_", name.lower()).strip("_") or name


def _has_typescript_markers(directory: Path) -> bool:
    return any((directory / m).exists() for m in TYPESCRIPT_MARKERS)
