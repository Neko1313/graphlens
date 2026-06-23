"""Shared fixtures for the PHP adapter test-suite."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from graphlens.contracts import Occurrence, ResolvedRef, SymbolResolver
from graphlens.status import ResolverStatus

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def make_project(tmp_path: Path):
    """Return a helper that writes a composer.json + PHP files to tmp_path."""

    def _make(
        files: dict[str, str],
        composer: dict | None = None,
    ) -> Path:
        if composer is not None:
            (tmp_path / "composer.json").write_text(
                json.dumps(composer), encoding="utf-8"
            )
        for rel, content in files.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return tmp_path

    return _make


class FakeResolver(SymbolResolver):
    """Resolver stub whose answers are scripted per (file_name, line, col).

    Maps a query position to a pre-built :class:`ResolvedRef` so the
    adapter's resolution pass can be exercised without a PHP language server.
    """

    def __init__(
        self,
        answers: dict[tuple[str, int, int], ResolvedRef] | None = None,
        status: ResolverStatus = ResolverStatus.OK,
    ) -> None:
        self._answers = answers or {}
        self._status = status

    def prepare(self, project_root: Path, files: list[Path]) -> None:
        pass

    def definition_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        return self._answers.get((file.name, line, col))

    def infer_type_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        return None

    def references_to(
        self, file: Path, line: int, col: int
    ) -> list[Occurrence]:
        return []

    def status(self) -> ResolverStatus:
        return self._status
