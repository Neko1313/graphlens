"""TyResolver — LSP-backed Python symbol resolver using ``ty server``."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote

from graphlens.contracts import Occurrence, ResolvedRef, SymbolResolver

logger = logging.getLogger("graphlens_python")


def _uri_to_path(uri: str) -> Path | None:
    """Convert a ``file://`` URI to a ``Path``; None for other schemes."""
    if not uri.startswith("file://"):
        return None
    return Path(unquote(uri[7:]))


class _TyLspClient:
    """Minimal synchronous LSP JSON-RPC client for ``ty server`` (stdio)."""

    def __init__(self, project_root: Path) -> None:
        ty_bin = shutil.which("ty") or "ty"
        self._proc: subprocess.Popen = subprocess.Popen(  # type: ignore[type-arg]
            [ty_bin, "server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(project_root),
        )
        self._next_id = 0
        self._opened_uris: set[str] = set()
        self._initialize(project_root)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

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
        """Read one LSP message, waiting at most *timeout* seconds."""
        if self._proc.stdout is None or self._proc.poll() is not None:
            return None
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not ready:
            logger.warning("ty server timed out after %.0fs", timeout)
            return None
        content_length = 0
        try:
            while True:
                raw = self._proc.stdout.readline()
                if not raw:
                    return None  # EOF — server exited
                stripped = raw.strip()
                if not stripped:
                    break  # blank line ends LSP headers
                if stripped.lower().startswith(b"content-length:"):
                    content_length = int(stripped.split(b":", 1)[1].strip())
            if not content_length:
                return {}
            body = self._proc.stdout.read(content_length)
            return json.loads(body) if body else {}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("ty server read error: %s", exc)
            return None

    def _recv_response(
        self, expected_id: int, timeout: float = 30.0
    ) -> dict | None:  # type: ignore[type-arg]
        """
        Consume messages until the one with *expected_id* arrives.

        Server-to-client requests (have both ``id`` and ``method``) receive a
        MethodNotFound error so ty does not stall waiting for a reply.
        Notifications (no ``id``) are silently discarded.
        """
        for _ in range(500):  # cap to prevent accidental infinite loop
            msg = self._read_one(timeout=timeout)
            if msg is None:
                return None
            msg_id = msg.get("id")
            if "method" in msg:
                # Server-to-client request — reply with error to unblock ty
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
                continue  # notification or server request — skip
            if msg_id == expected_id:
                return msg
        logger.warning("ty server did not respond to request %d", expected_id)
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

    # ------------------------------------------------------------------
    # LSP lifecycle
    # ------------------------------------------------------------------

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
        )
        if resp is not None:
            self._notify("initialized", {})

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

    def open_file(self, file: Path) -> str:
        """Open *file* in ty (idempotent) and return its ``file://`` URI."""
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
                        "languageId": "python",
                        "version": 1,
                        "text": text,
                    },
                },
            )
            # Wait for ty to finish analyzing the file before returning.
            # Without this, definition queries sent immediately after didOpen
            # block for 30+ seconds until ty's background analysis completes.
            self._drain_for_diagnostics(uri)
        return uri

    def _drain_for_diagnostics(self, uri: str, timeout: float = 5.0) -> None:
        """Drain messages until publishDiagnostics for *uri* (or timeout)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            msg = self._read_one(timeout=min(remaining, 1.0))
            if msg is None:
                break
            method = msg.get("method", "")
            msg_id = msg.get("id")
            if method and msg_id is not None:
                # Server-to-client request — reply with error to unblock ty
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32601, "message": "MethodNotFound"},
                    }
                )
            elif (
                method == "textDocument/publishDiagnostics"
                and msg.get("params", {}).get("uri") == uri
            ):
                return  # ty finished analyzing this file

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def definition(
        self, file: Path, line: int, col: int
    ) -> dict | None:  # type: ignore[type-arg]
        """Return the first LSP ``Location`` for 1-based *(line, col)*."""
        uri = self.open_file(file)
        resp = self._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line - 1, "character": col - 1},
            },
            timeout=30.0,
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
        """Return LSP ``Location`` list for 1-based *(line, col)*."""
        uri = self.open_file(file)
        resp = self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line - 1, "character": col - 1},
                "context": {"includeDeclaration": False},
            },
            timeout=30.0,
        )
        if resp is None:
            return []
        result = resp.get("result")
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

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


class TyResolver(SymbolResolver):
    """
    Resolve Python symbols via ``ty server`` (Astral ty, Rust-based).

    Spawns one ``ty server`` LSP subprocess per :meth:`prepare` call.
    Files are opened lazily on the first ``definition_at`` query — bulk
    pre-opening is intentionally avoided because sending hundreds of
    ``textDocument/didOpen`` notifications at once fills ty's stdin buffer
    and deadlocks on large projects.

    Requires ``ty`` to be in ``PATH``. Install from
    https://docs.astral.sh/ty/.  If ``ty`` is not found, :meth:`prepare`
    logs a warning and all queries return ``None``/``[]`` (the structural
    graph is still produced correctly).

    All methods return ``None``/``[]`` on any error — never raise.
    ``infer_type_at`` always returns ``None`` (type inference via LSP hover
    would require parsing ty's markdown output).
    """

    def __init__(self) -> None:
        self._client: _TyLspClient | None = None
        self._root: Path | None = None

    def prepare(self, project_root: Path, files: list[Path]) -> None:  # noqa: ARG002
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.shutdown()
            self._client = None
        self._root = project_root
        try:
            self._client = _TyLspClient(project_root)
        except Exception:
            logger.warning("Failed to start ty server for %s", project_root)
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
            return "stdlib"
        parts = file_path.parts
        if "typeshed" in parts:
            return "stdlib"
        if "site-packages" in parts or "dist-packages" in parts:
            return "third_party"
        if self._root is not None:
            with contextlib.suppress(ValueError):
                file_path.relative_to(self._root)
                return "internal"
        # Resolve symlinks before comparing: uv manages Pythons via symlinks
        # (cpython-3.13-... → cpython-3.13.5-...) and ty returns real paths.
        real = file_path.resolve()
        base = Path(sys.base_prefix).resolve()
        with contextlib.suppress(ValueError):
            real.relative_to(base)
            return "stdlib"
        return "unknown"

    def __del__(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.shutdown()
