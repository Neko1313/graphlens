"""Go symbol resolvers: a gopls LSP backend and a structure-only fallback."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import select
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

from graphlens.contracts import Occurrence, ResolvedRef, SymbolResolver
from graphlens.status import ResolverStatus

logger = logging.getLogger("graphlens_go")

# Consecutive path parts that mark Go's downloaded-module cache.
_MOD_CACHE = ("pkg", "mod")


def _uri_to_path(uri: str) -> Path | None:
    """Convert a ``file://`` URI to a ``Path``; None for other schemes."""
    if not uri.startswith("file://"):
        return None
    return Path(unquote(uri[7:]))


def _in_mod_cache(parts: tuple[str, ...]) -> bool:
    """Return True if ``parts`` contains the ``pkg/mod`` cache segment."""
    return any(
        parts[i : i + 2] == _MOD_CACHE for i in range(len(parts) - 1)
    )


def _detect_goroot() -> Path | None:  # pragma: no cover - needs go toolchain
    """Return ``go env GOROOT`` as a Path, or None if go is unavailable."""
    go_bin = shutil.which("go")
    if go_bin is None:
        return None
    try:
        out = subprocess.run(
            [go_bin, "env", "GOROOT"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    root = out.stdout.strip()
    return Path(root) if root else None


class _GoplsClient:  # pragma: no cover - subprocess transport (integration)
    """Minimal synchronous LSP JSON-RPC client for ``gopls`` (stdio)."""

    def __init__(self, project_root: Path) -> None:
        gopls_bin = shutil.which("gopls") or "gopls"
        self._proc: subprocess.Popen = subprocess.Popen(  # type: ignore[type-arg]
            [gopls_bin],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(project_root),
        )
        self._next_id = 0
        self._opened_uris: set[str] = set()
        self._initialize(project_root)

    def _write(self, msg: dict) -> None:  # type: ignore[type-arg]
        if self._proc.stdin is None or self._proc.poll() is not None:
            return
        body = json.dumps(msg, separators=(",", ":")).encode()
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        try:
            self._proc.stdin.write(header + body)
            self._proc.stdin.flush()
        except OSError:
            pass

    def _read_one(self, timeout: float = 30.0) -> dict | None:  # type: ignore[type-arg]
        if self._proc.stdout is None or self._proc.poll() is not None:
            return None
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not ready:
            logger.warning("gopls timed out after %.0fs", timeout)
            return None
        content_length = 0
        try:
            while True:
                raw = self._proc.stdout.readline()
                if not raw:
                    return None
                stripped = raw.strip()
                if not stripped:
                    break
                if stripped.lower().startswith(b"content-length:"):
                    content_length = int(stripped.split(b":", 1)[1].strip())
            if not content_length:
                return {}
            body = self._proc.stdout.read(content_length)
            return json.loads(body) if body else {}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("gopls read error: %s", exc)
            return None

    def _recv_response(
        self, expected_id: int, timeout: float = 30.0
    ) -> dict | None:  # type: ignore[type-arg]
        for _ in range(500):
            msg = self._read_one(timeout=timeout)
            if msg is None:
                return None
            msg_id = msg.get("id")
            if "method" in msg:
                if msg_id is not None:
                    self._write(
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "error": {
                                "code": -32601,
                                "message": "Method not found",
                            },
                        }
                    )
                continue
            if msg_id == expected_id:
                return msg
        logger.warning("gopls did not respond to request %d", expected_id)
        return None

    def _request(
        self, method: str, params: object, timeout: float = 30.0
    ) -> dict | None:  # type: ignore[type-arg]
        self._next_id += 1
        mid = self._next_id
        self._write(
            {"jsonrpc": "2.0", "id": mid, "method": method, "params": params}
        )
        return self._recv_response(mid, timeout=timeout)

    def _notify(self, method: str, params: object) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _initialize(self, project_root: Path) -> None:
        resp = self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": project_root.as_uri(),
                "capabilities": {
                    "textDocument": {
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                    },
                },
                "workspaceFolders": [
                    {"uri": project_root.as_uri(), "name": project_root.name},
                ],
            },
            timeout=60.0,
        )
        if resp is not None:
            self._notify("initialized", {})

    def open_file(self, file: Path) -> str:
        uri = file.as_uri()
        if uri not in self._opened_uris:
            self._opened_uris.add(uri)
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            self._notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "go",
                        "version": 1,
                        "text": text,
                    },
                },
            )
        return uri

    def definition(
        self, file: Path, line: int, col: int
    ) -> dict | None:  # type: ignore[type-arg]
        uri = self.open_file(file)
        resp = self._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line - 1, "character": col - 1},
            },
            timeout=60.0,
        )
        if resp is None:
            return None
        result = resp.get("result")
        if not result:
            return None
        return result[0] if isinstance(result, list) else result

    def references(
        self, file: Path, line: int, col: int
    ) -> list[dict]:  # type: ignore[type-arg]
        uri = self.open_file(file)
        resp = self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line - 1, "character": col - 1},
                "context": {"includeDeclaration": False},
            },
            timeout=60.0,
        )
        if resp is None:
            return []
        result = resp.get("result")
        return result if isinstance(result, list) else []

    def shutdown(self) -> None:
        if self._proc.poll() is not None:
            return
        try:
            self._request("shutdown", None)
            self._notify("exit", None)
            self._proc.wait(timeout=5)
        except Exception:
            with contextlib.suppress(Exception):
                self._proc.kill()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.shutdown()


class GoplsResolver(SymbolResolver):
    """
    Resolve Go symbols via ``gopls`` (the official Go LSP server).

    Spawns one ``gopls`` subprocess per :meth:`prepare` call. Files are
    opened lazily on the first query. Requires ``gopls`` (and the ``go``
    toolchain) in ``PATH``; if ``gopls`` is missing, :meth:`prepare` logs a
    warning and every query returns ``None``/``[]`` so the structural graph
    is still produced and :meth:`status` reports ``UNAVAILABLE``.

    All methods return ``None``/``[]`` on any error — never raise.
    ``infer_type_at`` always returns ``None``.
    """

    def __init__(self) -> None:
        self._client: _GoplsClient | None = None
        self._root: Path | None = None
        self._goroot: Path | None = None

    def prepare(self, project_root: Path, files: list[Path]) -> None:  # noqa: ARG002
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.shutdown()
            self._client = None
        self._root = project_root
        self._goroot = _detect_goroot()
        try:
            self._client = _GoplsClient(project_root)
        except Exception:
            logger.warning("Failed to start gopls for %s", project_root)
            self._client = None

    def definition_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        if self._client is None:
            return None
        try:
            loc = self._client.definition(file, line, col)
        except Exception:
            return None
        if loc is None:
            return None
        return self._loc_to_ref(loc)

    def infer_type_at(
        self, file: Path, line: int, col: int  # noqa: ARG002
    ) -> ResolvedRef | None:
        return None

    def references_to(
        self, file: Path, line: int, col: int
    ) -> list[Occurrence]:
        if self._client is None:
            return []
        try:
            locs = self._client.references(file, line, col)
        except Exception:
            return []
        out: list[Occurrence] = []
        for loc in locs:
            fp = _uri_to_path(loc.get("uri", ""))
            if fp is None:
                continue
            start = loc.get("range", {}).get("start", {})
            out.append(
                Occurrence(
                    file_path=fp,
                    line=start.get("line", 0) + 1,
                    col=start.get("character", 0) + 1,
                    is_definition=False,
                    access="unknown",
                )
            )
        return out

    def status(self) -> ResolverStatus:
        return (
            ResolverStatus.OK
            if self._client is not None
            else ResolverStatus.UNAVAILABLE
        )

    def _loc_to_ref(self, loc: dict) -> ResolvedRef:  # type: ignore[type-arg]
        fp = _uri_to_path(loc.get("uri", ""))
        start = loc.get("range", {}).get("start", {})
        return ResolvedRef(
            full_name="",
            file_path=fp,
            line=start.get("line", 0) + 1,
            col=start.get("character", 0) + 1,
            kind="",
            origin=self._classify(fp),
        )

    def _classify(self, file_path: Path | None) -> str:
        if file_path is None:
            return "unknown"
        if _in_mod_cache(file_path.parts):
            return "third_party"
        if self._root is not None:
            with contextlib.suppress(ValueError):
                file_path.relative_to(self._root)
                return "internal"
        if self._goroot is not None:
            with contextlib.suppress(ValueError):
                file_path.relative_to(self._goroot)
                return "stdlib"
        return "unknown"

    def __del__(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.shutdown()


class GoResolver(SymbolResolver):
    """
    Structure-only Go resolver that always degrades.

    Useful as an explicit fallback (and in tests) when a gopls-backed
    semantic layer is undesired: every query returns ``None``/``[]`` and
    :meth:`status` reports ``UNAVAILABLE`` so an adapter records a truthful
    ``resolver_status`` instead of implying a fully resolved result.
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
