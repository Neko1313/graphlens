"""ProjectReader contract and DiscoveredProject model."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class DiscoveredProject:
    """Result of project discovery: a root path, language, and source files."""

    root: Path
    language: str
    files: list[Path] = field(default_factory=list)


class ProjectReader(ABC):
    """Contract for project discovery and source file enumeration."""

    @abstractmethod
    def discover(self, root: Path) -> list[DiscoveredProject]:
        """
        Scan the root directory and return discovered projects.

        A monorepo may contain multiple projects (e.g., a Python backend and
        a TypeScript frontend). Each gets its own DiscoveredProject entry.
        """
        ...
