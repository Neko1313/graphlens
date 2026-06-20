"""Go dependency parsing (go.mod) and import-origin classification."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from graphlens.contracts import DependencyFileParser

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

_REQUIRE_BLOCK = re.compile(r"require\s*\((.*?)\)", re.DOTALL)
_REQUIRE_LINE = re.compile(r"^\s*require\s+(\S+)\s+\S+", re.MULTILINE)
_MODULE_RE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)


class GoModParser(DependencyFileParser):
    """Parse ``require`` directives (module paths) from a ``go.mod``."""

    def can_parse(self, project_root: Path) -> bool:
        return (project_root / "go.mod").is_file()

    def parse(self, project_root: Path) -> frozenset[str]:
        try:
            text = (project_root / "go.mod").read_text(encoding="utf-8")
        except OSError:
            return frozenset()
        modules: set[str] = set()
        for block in _REQUIRE_BLOCK.findall(text):
            for raw in block.splitlines():
                entry = raw.strip()
                if not entry or entry.startswith("//"):
                    continue
                modules.add(entry.split()[0])
        for match in _REQUIRE_LINE.finditer(text):
            modules.add(match.group(1))
        return frozenset(modules)


GO_DEFAULT_DEP_PARSERS: list[DependencyFileParser] = [GoModParser()]


def read_module_path(root: Path) -> str | None:
    """Return the module path from a ``go.mod`` ``module`` directive."""
    try:
        text = (root / "go.mod").read_text(encoding="utf-8")
    except OSError:
        return None
    match = _MODULE_RE.search(text)
    return match.group(1) if match else None


def classify_go_import(
    import_path: str, module_path: str | None, required: Iterable[str]
) -> str:
    """
    Classify a Go import path.

    Returns one of ``"stdlib"`` / ``"internal"`` / ``"third_party"`` /
    ``"unknown"``. Uses Go's own rule that a standard-library import path has
    no dot in its first path element (third-party paths start with a domain).
    """
    if module_path and (
        import_path == module_path
        or import_path.startswith(module_path + "/")
    ):
        return "internal"
    first = import_path.split("/", 1)[0]
    if "." not in first:
        return "stdlib"
    for req in required:
        if import_path == req or import_path.startswith(req + "/"):
            return "third_party"
    return "unknown"
