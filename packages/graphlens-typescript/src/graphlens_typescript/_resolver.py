"""TypeScript type-aware resolver via a Node subprocess (Compiler API)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from importlib.resources import files as _pkg_files
from pathlib import Path

from graphlens.contracts import Occurrence, ResolvedRef, SymbolResolver

logger = logging.getLogger("graphlens_typescript")

_TS_VERSION = "5.8.3"
Query = tuple[Path, int, int]  # (absolute file, 1-based line, 1-based col)

_BRIDGE_JS = "ts_resolver.js"
_BRIDGE_PKG = "_ts_bridge_package.json"


def _cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "graphlens" / "ts-resolver"


class TsResolver(SymbolResolver):
    """Resolves TS symbols via a bundled Node script. Never raises."""

    def __init__(self, ts_version: str = _TS_VERSION) -> None:
        """Initialise resolver with a pinned typescript version."""
        self._ts_version = ts_version
        self._root: Path | None = None
        self._cache_dir: Path = _cache_root() / ts_version
        self._disabled = False

    def prepare(self, project_root: Path, files: list[Path]) -> None:  # noqa: ARG002
        """Set up the engine for a project before any queries."""
        self._root = project_root
        try:
            self.ensure_typescript()
        except Exception:
            logger.warning("TsResolver disabled: typescript unavailable")
            self._disabled = True

    def ensure_typescript(self) -> None:
        """Install typescript into the cache dir if not already present."""
        sentinel = (
            self._cache_dir / "node_modules" / "typescript"
            / "lib" / "typescript.js"
        )
        if sentinel.exists():
            return
        if shutil.which("node") is None or shutil.which("npm") is None:
            msg = "node/npm not found"
            raise RuntimeError(msg)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        (self._cache_dir / "package.json").write_text('{"private":true}')
        subprocess.run(
            ["npm", "install",  # noqa: S607
             f"typescript@{self._ts_version}",
             "--no-save", "--no-audit", "--prefer-offline"],
            cwd=str(self._cache_dir),
            check=True, capture_output=True, timeout=300,
        )

    def resolve_all(
        self, queries: list[Query]
    ) -> list[ResolvedRef | None]:
        """Resolve a batch of positions; never raises."""
        if self._disabled or not queries or self._root is None:
            return [None] * len(queries)
        try:
            payload = self._run_bridge(self._build_request(queries))
            return self._parse_response(payload)
        except Exception:
            logger.warning("TsResolver batch failed; degrading to None")
            return [None] * len(queries)

    def definition_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        """Resolve the symbol at a position to its definition (cross-file)."""
        return self.resolve_all([(file, line, col)])[0]

    def infer_type_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        """Infer the type of the expression at a position."""
        return self.resolve_all([(file, line, col)])[0]

    def references_to(self, file: Path, line: int, col: int) -> list[Occurrence]:  # noqa: ARG002, E501
        """Return all references to the symbol at a position."""
        return []  # references batch not used by the resolution pass; deferred

    def _build_request(self, queries: list[Query]) -> dict:
        """Build the JSON request payload for the Node bridge."""
        return {
            "project_root": str(self._root),
            "queries": [
                {"file": str(f), "line": ln, "col": c}
                for (f, ln, c) in queries
            ],
        }

    def _parse_response(
        self, payload: dict
    ) -> list[ResolvedRef | None]:
        """Map the bridge JSON response to a list of ResolvedRef or None."""
        out: list[ResolvedRef | None] = []
        for item in payload.get("results", []):
            if not item:
                out.append(None)
                continue
            out.append(ResolvedRef(
                full_name=item.get("name", ""),
                file_path=Path(item["file"]) if item.get("file") else None,
                line=item.get("line", 1),
                col=item.get("col", 1),
                kind=item.get("kind", "unknown"),
                origin=item.get("origin", "unknown"),
            ))
        return out

    def _run_bridge(self, request: dict) -> dict:
        """Invoke the Node bridge as a subprocess, return parsed JSON."""
        bridge = _pkg_files("graphlens_typescript") / _BRIDGE_JS
        env = dict(os.environ, TS_CACHE_DIR=str(self._cache_dir))
        completed = subprocess.run(
            ["node", str(bridge)],  # noqa: S607
            input=json.dumps(request),
            capture_output=True, text=True, env=env,
            cwd=str(self._root), timeout=600, check=False,
        )
        return json.loads(completed.stdout)
