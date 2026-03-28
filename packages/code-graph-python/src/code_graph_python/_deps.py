"""Dependency file parsers for Python projects."""

from __future__ import annotations

import configparser
import re
import sys
from typing import TYPE_CHECKING

import tomllib
from code_graph.contracts import DependencyFileParser, normalize_pkg_name

if TYPE_CHECKING:
    from pathlib import Path


class PyprojectDepsParser(DependencyFileParser):
    """
    Reads declared dependencies from ``pyproject.toml``.

    Supports:

    - PEP 621: ``[project.dependencies]`` and
      ``[project.optional-dependencies]``
    - Poetry: ``[tool.poetry.dependencies]`` and
      ``[tool.poetry.group.*.dependencies]``

    Dev / test dependency groups are included so that test-only imports
    (e.g. ``pytest``) are classified as ``third_party`` rather than
    ``unknown``.
    """

    def can_parse(self, project_root: Path) -> bool:
        return (project_root / "pyproject.toml").exists()

    def parse(  # noqa: PLR0912
        self, project_root: Path
    ) -> frozenset[str]:
        path = project_root / "pyproject.toml"
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError):
            return frozenset()

        names: set[str] = set()

        # PEP 621 -------------------------------------------------------
        project = data.get("project", {})
        for dep in project.get("dependencies", []):
            n = normalize_pkg_name(dep)
            if n:
                names.add(n)
        for group_deps in project.get("optional-dependencies", {}).values():
            for dep in group_deps:
                n = normalize_pkg_name(dep)
                if n:
                    names.add(n)

        # Poetry --------------------------------------------------------
        poetry = data.get("tool", {}).get("poetry", {})
        for dep in poetry.get("dependencies", {}):
            n = normalize_pkg_name(dep)
            if n and n != "python":
                names.add(n)
        for dep in poetry.get("dev-dependencies", {}):
            n = normalize_pkg_name(dep)
            if n:
                names.add(n)
        # Poetry dependency groups (poetry >= 1.2)
        for group in poetry.get("group", {}).values():
            for dep in group.get("dependencies", {}):
                n = normalize_pkg_name(dep)
                if n:
                    names.add(n)

        return frozenset(names)


class RequirementsTxtParser(DependencyFileParser):
    """
    Reads ``requirements*.txt`` files in the project root.

    Handles:

    - Plain package names: ``requests``
    - Version specifiers: ``requests>=2.28``
    - Extras: ``requests[security]``
    - URL / VCS requirements: skipped (no importable name can be reliably
      extracted)
    - ``-r other.txt`` recursive includes: followed one level deep
    - Inline comments: stripped
    """

    # Matches lines that are URL or VCS requirements (skip them)
    _SKIP_RE = re.compile(r"^\s*(-r|-c|-e|https?://|git\+|svn\+|hg\+|bzr\+)")

    def can_parse(self, project_root: Path) -> bool:
        return any(project_root.glob("requirements*.txt"))

    def parse(self, project_root: Path) -> frozenset[str]:
        names: set[str] = set()
        for req_file in sorted(project_root.glob("requirements*.txt")):
            self._parse_file(req_file, names, project_root)
        return frozenset(names)

    def _parse_file(self, path: Path, names: set[str], root: Path) -> None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if self._SKIP_RE.match(line):
                # Follow -r includes one level deep
                if line.startswith("-r"):
                    ref = line[2:].strip()
                    ref_path = root / ref
                    if ref_path.exists():
                        self._parse_file(ref_path, names, root)
                continue
            n = normalize_pkg_name(line)
            if n:
                names.add(n)


class SetupCfgDepsParser(DependencyFileParser):
    """Reads ``[options] install_requires`` from ``setup.cfg``."""

    def can_parse(self, project_root: Path) -> bool:
        return (project_root / "setup.cfg").exists()

    def parse(self, project_root: Path) -> frozenset[str]:
        path = project_root / "setup.cfg"
        cfg = configparser.ConfigParser()
        try:
            cfg.read(path, encoding="utf-8")
        except (configparser.Error, OSError):
            return frozenset()

        names: set[str] = set()
        raw = cfg.get("options", "install_requires", fallback="")
        for line in raw.splitlines():
            n = normalize_pkg_name(line)
            if n:
                names.add(n)
        # extras_require
        for section in cfg.sections():
            if section.startswith("options.extras_require"):
                raw_extras = cfg.get(
                    section, "install_requires", fallback=""
                )
                for line in raw_extras.splitlines():
                    n = normalize_pkg_name(line)
                    if n:
                        names.add(n)
        return frozenset(names)


# ---------------------------------------------------------------------------
# Default parser list for PythonAdapter
# ---------------------------------------------------------------------------

PYTHON_DEFAULT_DEP_PARSERS: list[DependencyFileParser] = [
    PyprojectDepsParser(),
    RequirementsTxtParser(),
    SetupCfgDepsParser(),
]


# ---------------------------------------------------------------------------
# Stdlib set (Python 3.10+)
# ---------------------------------------------------------------------------

def get_stdlib_names() -> frozenset[str]:
    """Return stdlib top-level module names for the running Python."""
    # sys.stdlib_module_names is available from Python 3.10
    stdlib: set[str] = set(getattr(sys, "stdlib_module_names", set()))
    # Add a minimal hardcoded set as fallback for older Pythons
    stdlib.update({
        "abc", "ast", "asyncio", "builtins", "collections", "contextlib",
        "copy", "dataclasses", "datetime", "enum", "functools", "hashlib",
        "importlib", "inspect", "io", "itertools", "json", "logging",
        "math", "operator", "os", "pathlib", "pickle", "re", "shutil",
        "signal", "socket", "string", "struct", "subprocess", "sys",
        "tempfile", "threading", "time", "tomllib", "traceback", "typing",
        "unittest", "urllib", "uuid", "warnings", "weakref",
    })
    return frozenset(stdlib)
