"""jedi-backed SymbolResolver for Python — precise cross-file resolution."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import jedi
import jedi.api.classes
from graphlens.contracts import Occurrence, ResolvedRef, SymbolResolver

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("graphlens_python")

# jedi: line 1-based, column 0-based. graphlens: both 1-based.


class JediResolver(SymbolResolver):
    """
    Resolve Python symbols via jedi.

    Never raises; returns None/[] on miss.
    """

    def __init__(self, stdlib_names: frozenset[str]) -> None:
        self._stdlib_names = stdlib_names
        self._project: jedi.Project | None = None
        self._root: Path | None = None

    def prepare(self, project_root: Path, files: list[Path]) -> None:  # noqa: ARG002
        """Set up jedi.Project for a project root before any queries."""
        self._root = project_root
        try:
            self._project = jedi.Project(str(project_root))
        except Exception:
            logger.warning("jedi.Project failed for %s", project_root)
            self._project = None

    def _script(self, file: Path) -> jedi.Script | None:
        if self._project is None:
            return None
        try:
            return jedi.Script(path=str(file), project=self._project)
        except Exception:
            return None

    def definition_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        """Resolve the symbol at a position to its definition (cross-file)."""
        script = self._script(file)
        if script is None:
            return None
        try:
            names = script.goto(line, col - 1, follow_imports=True)
        except Exception:
            return None
        return self._to_ref(names[0]) if names else None

    def infer_type_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        """Infer the type of the expression at a position."""
        script = self._script(file)
        if script is None:
            return None
        try:
            names = script.infer(line, col - 1)
        except Exception:
            return None
        return self._to_ref(names[0]) if names else None

    def references_to(
        self, file: Path, line: int, col: int
    ) -> list[Occurrence]:
        """Return all references to the symbol at a position."""
        script = self._script(file)
        if script is None:
            return []
        try:
            names = script.get_references(line, col - 1, scope="project")
        except Exception:
            return []
        out: list[Occurrence] = []
        for n in names:
            if n.module_path is None or n.line is None:
                continue
            out.append(
                Occurrence(
                    file_path=n.module_path,
                    line=n.line,
                    col=(n.column or 0) + 1,
                    is_definition=n.is_definition(),
                    access="unknown",
                )
            )
        return out

    def _to_ref(self, name: jedi.api.classes.Name) -> ResolvedRef:
        """Convert a jedi Name to a ResolvedRef with 1-based coordinates."""
        in_builtin = bool(name.in_builtin_module())
        module_path = name.module_path
        full_name = name.full_name or name.name
        return ResolvedRef(
            full_name=full_name or "",
            file_path=module_path,
            line=name.line or 1,
            col=(name.column or 0) + 1,
            kind=name.type,
            origin=self._classify(module_path, full_name, in_builtin),
        )

    def _classify(
        self,
        module_path: Path | None,
        full_name: str | None,
        in_builtin: bool,
    ) -> str:
        """Classify origin: stdlib | internal | third_party | unknown."""
        if module_path is None or in_builtin:
            return "stdlib"
        if self._root is not None:
            try:
                module_path.relative_to(self._root)
                return "internal"
            except ValueError:
                pass
        parts = module_path.parts
        if "site-packages" in parts or "dist-packages" in parts:
            return "third_party"
        top = (full_name or "").split(".")[0]
        if top in self._stdlib_names:
            return "stdlib"
        return "unknown"
