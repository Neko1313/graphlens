"""Rust dependency parsing (Cargo.toml) and import-origin classification."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, cast

from graphlens.contracts import DependencyFileParser

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

_DEP_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")
_STDLIB_CRATES = frozenset(
    {"std", "core", "alloc", "proc_macro", "test"}
)
_INTERNAL_ROOTS = frozenset({"crate", "self", "super"})


def _load_cargo(project_root: Path) -> dict[str, object] | None:
    try:
        text = (project_root / "Cargo.toml").read_text(encoding="utf-8")
        return tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError):
        return None


class CargoTomlParser(DependencyFileParser):
    """Parse dependency crate names from a ``Cargo.toml``."""

    def can_parse(self, project_root: Path) -> bool:
        return (project_root / "Cargo.toml").is_file()

    def parse(self, project_root: Path) -> frozenset[str]:
        data = _load_cargo(project_root)
        if data is None:
            return frozenset()
        names: set[str] = set()
        for table in _DEP_TABLES:
            section = data.get(table)
            if isinstance(section, dict):
                names |= {str(k).replace("-", "_") for k in section}
        return frozenset(names)


RUST_DEFAULT_DEP_PARSERS: list[DependencyFileParser] = [CargoTomlParser()]


def read_crate_name(project_root: Path) -> str | None:
    """Return the crate name from ``[package].name`` in Cargo.toml."""
    data = _load_cargo(project_root)
    if data is None:
        return None
    pkg = data.get("package")
    if isinstance(pkg, dict):
        name = cast("dict[str, object]", pkg).get("name")
        if isinstance(name, str):
            return name
    return None


def classify_rust_import(
    import_path: str, crate_name: str | None, deps: Iterable[str]
) -> str:
    """
    Classify a Rust ``use`` path.

    Returns ``"stdlib"`` / ``"internal"`` / ``"third_party"`` /
    ``"unknown"``. ``crate``/``self``/``super`` and the crate's own name are
    internal; ``std``/``core``/``alloc`` are stdlib; anything listed in
    Cargo dependency tables is third-party (crate names normalize hyphens to
    underscores to match import syntax).
    """
    first = import_path.split("::", 1)[0].strip()
    if first in _STDLIB_CRATES:
        return "stdlib"
    if first in _INTERNAL_ROOTS:
        return "internal"
    norm = first.replace("-", "_")
    if crate_name and norm == crate_name.replace("-", "_"):
        return "internal"
    if norm in set(deps):
        return "third_party"
    return "unknown"
