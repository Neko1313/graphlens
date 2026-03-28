"""DependencyFileParser contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class DependencyFileParser(ABC):
    """
    Extracts declared third-party dependency names from a project manifest.

    Each implementation targets one file format (pyproject.toml, package.json,
    requirements.txt, Cargo.toml, …).  Language adapters ship default parsers
    for their ecosystem; users can pass custom parsers to handle non-standard
    package managers (poetry, pnpm workspaces, pip-tools, etc.).

    ``parse()`` returns *normalized* top-level distribution names so that
    callers can compare them against the first segment of an import path::

        "requests"          # PyPI
        "scikit_learn"      # normalized: scikit-learn → scikit_learn
        "@types/node"       # npm scoped package (keep as-is)

    Normalization rule: lowercase, hyphens → underscores, drop extras/version
    specifiers.  Scoped npm names (``@scope/pkg``) are kept unchanged.
    """

    @abstractmethod
    def can_parse(self, project_root: Path) -> bool:
        """Return True if this parser applies to the given project root."""
        ...

    @abstractmethod
    def parse(self, project_root: Path) -> frozenset[str]:
        """Return normalized top-level package names declared as deps."""
        ...


def normalize_pkg_name(name: str) -> str:
    """
    Normalize a distribution name for import-name comparison.

    * Strips version specifiers and extras:
      ``requests>=2.0 [security]`` → ``requests``
    * Lowercases
    * Replaces hyphens with underscores

    Scoped npm names (``@scope/pkg``) are returned as-is (lowercased).
    """
    # Strip inline comments (requirements.txt style)
    name = name.split("#", maxsplit=1)[0].strip()
    # Strip extras and version specifiers: Foo[bar]>=1.0 → Foo
    for sep in ("[", ">", "<", "=", "!", "~", ";", " "):
        name = name.split(sep)[0]
    name = name.strip()
    if not name:
        return ""
    # Scoped npm packages keep their structure
    if name.startswith("@"):
        return name.lower()
    return name.lower().replace("-", "_")
