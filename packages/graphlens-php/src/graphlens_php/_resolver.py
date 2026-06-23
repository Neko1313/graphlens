"""
PHP symbol resolvers.

``PhpactorResolver`` drives a ``phpactor language-server`` subprocess over
stdio (the same LSP pattern the Python adapter uses for ``ty``). phpactor is
open-source (MIT) and implements ``textDocument/definition`` — the one query
the resolution pass needs. ``PhpResolver`` is a structure-only fallback that
always reports :data:`ResolverStatus.UNAVAILABLE`.

Both resolvers never raise: every error returns ``None``/``[]`` so the
structural graph is always produced, only the type-aware edges degrade.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import select
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import unquote

from graphlens.contracts import Occurrence, Query, ResolvedRef, SymbolResolver
from graphlens.status import ResolverStatus

logger = logging.getLogger("graphlens_php")


def _uri_to_path(uri: str) -> Path | None:
    """Convert a ``file://`` URI to a ``Path``; None for other schemes."""
    if not uri.startswith("file://"):
        return None
    return Path(unquote(uri[7:]))


class _PhpactorLspClient:  # pragma: no cover - integration transport
    """Minimal synchronous LSP JSON-RPC client for ``phpactor`` (stdio)."""

    def __init__(self, project_root: Path) -> None:
        php_bin = (
            os.environ.get("GRAPHLENS_PHPACTOR")
            or shutil.which("phpactor")
            or "phpactor"
        )
        self._proc: subprocess.Popen = subprocess.Popen(  # type: ignore[type-arg]
            [php_bin, "language-server", "--no-ansi"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(project_root),
        )
        self._next_id = 0
        self._opened_uris: set[str] = set()
        # Serialize stdin writes: the pipelined batch runs a writer thread
        # while the main thread may still write MethodNotFound replies, so
        # two threads can race on stdin and interleave a frame's bytes.
        self._write_lock = threading.Lock()
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
            with self._write_lock:
                self._proc.stdin.write(header + body)
                self._proc.stdin.flush()
        except OSError:
            pass

    def _read_one(self, timeout: float = 30.0) -> dict | None:  # type: ignore[type-arg]
        if self._proc.stdout is None or self._proc.poll() is not None:
            return None
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not ready:
            logger.warning("phpactor timed out after %.0fs", timeout)
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
            logger.debug("phpactor read error: %s", exc)
            return None

    def _recv_response(
        self, expected_id: int, timeout: float = 30.0
    ) -> dict | None:  # type: ignore[type-arg]
        for _ in range(500):  # cap to prevent accidental infinite loop
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
        logger.warning("phpactor did not respond to request %d", expected_id)
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
            timeout=60.0,
        )
        if resp is not None:
            self._notify("initialized", {})

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------

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
                        "languageId": "php",
                        "version": 1,
                        "text": text,
                    },
                },
            )
        return uri

    def open_files(self, files: list[Path]) -> None:
        """
        Send ``didOpen`` for every not-yet-opened file in one pass.

        Mirrors the per-file :meth:`open_file` (no diagnostics drain — the
        server processes ``didOpen`` before the ``definition`` requests that
        follow it on the same in-order stream), but batched so a whole project
        is opened up front instead of lazily one query at a time.
        """
        for file in files:
            self.open_file(file)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

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
            timeout=30.0,
        )
        if resp is None:
            return None
        result = resp.get("result")
        if not result:
            return None
        return result[0] if isinstance(result, list) else result

    @staticmethod
    def _first_location(result: object) -> dict | None:  # type: ignore[type-arg]
        """Reduce an LSP definition result to a single Location or None."""
        if not result:
            return None
        if isinstance(result, list):
            return result[0] if result else None
        return result  # type: ignore[return-value]

    def definition_batch(
        self, queries: list[Query]
    ) -> list[dict | None]:  # type: ignore[type-arg]
        """
        Resolve many positions in one pipelined exchange.

        Writes every ``textDocument/definition`` request up front (from a
        writer thread so a full stdin/stdout pipe can't deadlock against our
        reads), then collects responses by JSON-RPC id. Order is preserved;
        unanswered positions stay ``None``. This turns N blocking round-trips
        into one pipelined stream — the entire point of the batch path.
        """
        if not queries:
            return []
        self.open_files([f for (f, _l, _c) in queries])
        results: list[dict | None] = [None] * len(queries)
        if self._proc.poll() is not None:
            return results
        id2idx: dict[int, int] = {}
        reqs: list[dict] = []  # type: ignore[type-arg]
        for k, (file, line, col) in enumerate(queries):
            self._next_id += 1
            mid = self._next_id
            id2idx[mid] = k
            reqs.append(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "method": "textDocument/definition",
                    "params": {
                        "textDocument": {"uri": file.as_uri()},
                        "position": {
                            "line": line - 1,
                            "character": col - 1,
                        },
                    },
                }
            )

        def _writer() -> None:
            for req in reqs:
                self._write(req)

        writer = threading.Thread(target=_writer, daemon=True)
        writer.start()
        got = 0
        while got < len(queries):
            msg = self._read_one(timeout=60.0)
            if msg is None:
                break
            mid = msg.get("id")
            if "method" in msg:
                if mid is not None:
                    self._write(
                        {
                            "jsonrpc": "2.0",
                            "id": mid,
                            "error": {
                                "code": -32601,
                                "message": "Method not found",
                            },
                        }
                    )
                continue
            idx = id2idx.get(mid) if mid is not None else None
            if idx is not None:
                results[idx] = self._first_location(msg.get("result"))
                got += 1
        writer.join(timeout=5)
        return results

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


class PhpactorResolver(SymbolResolver):
    """
    Resolve PHP symbols via a ``phpactor language-server`` subprocess.

    Spawns one server per :meth:`prepare` call. Requires ``phpactor`` in
    ``PATH`` (or pointed to by ``$GRAPHLENS_PHPACTOR``) and a working PHP
    runtime. If phpactor cannot be started, :meth:`prepare` logs a warning
    and all queries return ``None``/``[]`` — the structural graph is still
    produced. ``infer_type_at`` always returns ``None``.
    """

    def __init__(self) -> None:
        self._client: _PhpactorLspClient | None = None
        self._root: Path | None = None

    def prepare(self, project_root: Path, files: list[Path]) -> None:  # noqa: ARG002
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.shutdown()
            self._client = None
        self._root = project_root
        try:
            self._client = _PhpactorLspClient(project_root)
        except Exception:
            logger.warning("Failed to start phpactor for %s", project_root)
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

    def resolve_all(self, queries: list[Query]) -> list[ResolvedRef | None]:
        """
        Resolve every occurrence in one pipelined phpactor exchange.

        Overrides the base per-query loop: on a large project the resolution
        pass issues one query per occurrence (hundreds of thousands on a big
        monorepo), and a blocking round-trip each would dominate analysis.
        Batching writes them all up front and reads responses by id, so the
        cost collapses to the server's throughput instead of the sum of
        per-request latencies.
        """
        if self._client is None:
            return [None] * len(queries)
        try:
            locs = self._client.definition_batch(queries)
        except Exception:
            return [None] * len(queries)
        return [
            self._loc_to_ref(loc) if loc is not None else None
            for loc in locs
        ]

    def infer_type_at(
        self, file: Path, line: int, col: int  # noqa: ARG002
    ) -> ResolvedRef | None:
        return None

    def status(self) -> ResolverStatus:
        return (
            ResolverStatus.OK
            if self._client is not None
            else ResolverStatus.UNAVAILABLE
        )

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
        if "vendor" in parts:
            return "third_party"
        if self._root is not None:
            with contextlib.suppress(ValueError):
                file_path.relative_to(self._root)
                return "internal"
        return "unknown"

    def __del__(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.shutdown()


class PhpResolver(SymbolResolver):
    """
    Structure-only fallback resolver.

    Produces no type-aware edges and always reports
    :data:`ResolverStatus.UNAVAILABLE`. Use it to build the structural graph
    without a phpactor / PHP toolchain available.
    """

    def prepare(self, project_root: Path, files: list[Path]) -> None:
        pass

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
