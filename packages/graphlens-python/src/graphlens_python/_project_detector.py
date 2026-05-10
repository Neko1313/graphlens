"""Python project detection: marker files and project name extraction."""

from __future__ import annotations

import configparser
import tomllib
from typing import TYPE_CHECKING

from graphlens.utils import collect_marker_roots

if TYPE_CHECKING:
    from pathlib import Path

PYTHON_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "requirements.txt",
)

_EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".venv", "venv", "__pycache__", ".git",
    "dist", "build", ".eggs", "node_modules",
})


def is_python_project(project_root: Path) -> bool:
    """
    Return True if the directory contains Python source files.

    Detection order:
    1. Python-specific marker files (pyproject.toml, setup.py, etc.)
    2. Fallback: any .py file exists anywhere under project_root

    The fallback handles multi-language projects (e.g. a monorepo root
    that has no Python markers but contains Python sub-packages alongside
    JS/Rust code). For pyproject.toml, also verifies the file contains a
    [project] section to avoid false positives from Rust projects that
    use pyproject.toml for tools.
    """
    if _has_python_markers(project_root):
        return True
    # Fallback: presence of any .py file is enough
    return any(project_root.rglob("*.py"))


def find_python_roots(search_root: Path) -> list[Path]:
    """
    Find the actual Python project roots within search_root.

    Walks for marker files and returns their parent directories — one per
    distinct Python sub-project. A marker at ``search_root`` does not hide
    nested marker roots. This ensures that ``detect_project_name`` and
    source-root resolution use the *correct* root rather than treating the
    whole monorepo as one project.

    Falls back to ``[search_root]`` when no markers are found anywhere (the
    directory contains only bare .py scripts with no packaging metadata).
    """
    return collect_marker_roots(
        search_root,
        PYTHON_MARKERS,
        excluded_dirs=_EXCLUDED_DIRS,
        marker_filter=_is_valid_python_marker,
    )


def detect_project_name(project_root: Path) -> str:
    """
    Extract the project name.

    Resolution order:
    1. pyproject.toml [project].name
    2. setup.cfg [metadata] name
    3. project_root directory name
    """
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        try:
            with pyproject.open("rb") as f:
                data = tomllib.load(f)
            name = data.get("project", {}).get("name")
            if name:
                return str(name)
        except (tomllib.TOMLDecodeError, OSError):
            pass

    setup_cfg = project_root / "setup.cfg"
    if setup_cfg.exists():
        try:
            cfg = configparser.ConfigParser()
            cfg.read(setup_cfg)
            name = cfg.get("metadata", "name", fallback=None)
            if name:
                return name.strip()
        except (configparser.Error, OSError):
            pass

    return project_root.name


def _has_python_markers(directory: Path) -> bool:
    """Return True if directory contains Python project marker files."""
    for marker in PYTHON_MARKERS:
        path = directory / marker
        if not path.exists():
            continue
        if _is_valid_python_marker(path):
            return True
    return False


def _is_valid_python_marker(path: Path) -> bool:
    if path.name == "pyproject.toml":
        return _pyproject_has_project_section(path)
    return True


def _pyproject_has_project_section(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
        return "project" in data
    except (tomllib.TOMLDecodeError, OSError):
        # If we can't parse it, check if .py files exist nearby as a fallback
        return any(path.parent.rglob("*.py"))
