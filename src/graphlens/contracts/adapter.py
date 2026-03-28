"""LanguageAdapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from graphlens.models.graph import GraphLens

_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        ".venv", "venv", "__pycache__", ".git",
        "dist", "build", ".eggs", "node_modules",
    }
)


class LanguageAdapter(ABC):
    """Contract that every language adapter package must implement."""

    @abstractmethod
    def language(self) -> str:
        """Return the language identifier, e.g. 'python', 'typescript'."""
        ...

    @abstractmethod
    def can_handle(self, project_root: Path) -> bool:
        """
        Return True if this adapter can handle the project at the given root.

        Typically checks for marker files
        (pyproject.toml, package.json, Cargo.toml).
        """
        ...

    @abstractmethod
    def analyze(
        self, project_root: Path, files: list[Path] | None = None
    ) -> GraphLens:
        """
        Parse the project and return a GraphLens with nodes and relations.

        If ``files`` is None, the adapter collects source files itself via
        ``collect_files()``. Pass an explicit list to override (e.g. for
        incremental updates or custom filtering in a pipeline).

        Adapters must not write to any backend — they return data only.
        """
        ...

    def file_extensions(self) -> set[str]:
        """
        Return file extensions this adapter handles, e.g. {'.py'}.

        Used by ``collect_files()`` for automatic discovery.
        """
        return set()

    def collect_files(self, project_root: Path) -> list[Path]:
        """
        Return all source files under project_root for this adapter.

        Excludes common non-source directories
        (.venv, __pycache__, .git, etc.).
        Override for custom discovery logic.
        """
        extensions = self.file_extensions()
        if not extensions:
            return []
        return sorted(
            p
            for p in project_root.rglob("*")
            if p.is_file()
            and p.suffix in extensions
            and not (_EXCLUDED_DIRS & set(p.relative_to(project_root).parts))
        )
