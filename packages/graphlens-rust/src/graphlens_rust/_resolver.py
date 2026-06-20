"""RustResolver — structure-only resolver for Rust (semantic layer staged)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphlens.contracts import Occurrence, ResolvedRef, SymbolResolver
from graphlens.status import ResolverStatus

if TYPE_CHECKING:
    from pathlib import Path


class RustResolver(SymbolResolver):
    """
    Structure-only resolver for Rust.

    Rust structure (modules, types, traits, functions, impls, imports) is
    extracted via tree-sitter without a type engine. Semantic edges
    (CALLS / REFERENCES / HAS_TYPE) require a rust-analyzer-backed generic
    LSP resolver, which is staged separately. Until then this resolver
    reports ``UNAVAILABLE`` so an adapter records a truthful
    ``resolver_status`` instead of implying a fully resolved result.

    All methods return ``None`` / ``[]`` and never raise.
    """

    def prepare(self, project_root: Path, files: list[Path]) -> None:  # noqa: ARG002
        return

    def definition_at(
        self, file: Path, line: int, col: int  # noqa: ARG002
    ) -> ResolvedRef | None:
        return None

    def infer_type_at(
        self, file: Path, line: int, col: int  # noqa: ARG002
    ) -> ResolvedRef | None:
        return None

    def references_to(
        self, file: Path, line: int, col: int  # noqa: ARG002
    ) -> list[Occurrence]:
        return []

    def status(self) -> ResolverStatus:
        return ResolverStatus.UNAVAILABLE
