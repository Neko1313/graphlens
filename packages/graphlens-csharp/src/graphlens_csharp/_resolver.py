"""
C# symbol resolver.

``CsharpLspResolver`` drives a ``csharp-ls`` subprocess — Razzmatazz's
Roslyn-based C# language server, installable as a .NET global tool
(``dotnet tool install --global csharp-ls``) — over stdio via
:class:`_CsharpLspClient`. It uses ``textDocument/definition`` and
``textDocument/references``; both come from Roslyn's semantic model, so the
resolved definitions are type-aware (correct method overload, base type, field
declaration) rather than name-matched.

Point ``$GRAPHLENS_CSHARP_LS`` at the binary, or have ``csharp-ls`` on
``PATH``. csharp-ls loads the solution/project via Roslyn on ``initialize``;
the client waits for the workspace-load progress to end before issuing
queries. When the binary (or the .NET runtime it needs) is absent it degrades
automatically: :meth:`CsharpLspResolver.status` reports
:data:`ResolverStatus.UNAVAILABLE` and every query returns ``None``/``[]``, so
the structural graph is still produced with only the type-aware edges dropped.

The resolver never raises: every error returns ``None``/``[]``.
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
import time
from pathlib import Path
from urllib.parse import unquote

from graphlens.contracts import Occurrence, Query, ResolvedRef, SymbolResolver
from graphlens.status import ResolverStatus

logger = logging.getLogger("graphlens_csharp")


def _uri_to_path(uri: str) -> Path | None:
    """
    Convert a ``file://`` URI to a ``Path``; None for other schemes.

    csharp-ls returns ``csharp:/metadata/...`` URIs for symbols resolved into
    compiled assemblies (BCL / NuGet); those are not files, so they map to
    ``None`` and are treated as external.
    """
    if not uri.startswith("file://"):
        return None
    return Path(unquote(uri[7:]))


class _CsharpLspClient:  # pragma: no cover - integration transport
    """
    Minimal synchronous LSP JSON-RPC client over stdio for csharp-ls.

    Holds only the JSON-RPC framing, lifecycle, and pipelined batch. The spawn
    ``argv`` and ``name`` (log messages only) are passed in by
    :class:`CsharpLspResolver`.
    """

    def __init__(
        self,
        project_root: Path,
        argv: list[str],
        name: str = "csharp-lsp",
    ) -> None:
        self._name = name
        self._proc: subprocess.Popen = subprocess.Popen(  # type: ignore[type-arg]
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(project_root),
        )
        self._next_id = 0
        self._opened_uris: set[str] = set()
        self._write_lock = threading.Lock()
        # Per-query read budget. Kept short so an unresponsive server (e.g.
        # a solution that never finished loading) is abandoned in seconds
        # rather than blocking the whole analysis; override for huge
        # solutions via $GRAPHLENS_CSHARP_LS_TIMEOUT.
        try:
            self._query_timeout = float(
                os.environ.get("GRAPHLENS_CSHARP_LS_TIMEOUT", "30")
            )
        except ValueError:
            self._query_timeout = 30.0
        # False once a batch ends with the server having failed to answer
        # every request — the resolver reads this to trip its circuit breaker.
        self.responsive = True
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

    def _read_frame(self) -> dict | None:  # type: ignore[type-arg]
        """Read one LSP frame from stdout (caller guarantees data is ready)."""
        stdout = self._proc.stdout
        if stdout is None:  # pragma: no cover - defensive
            return None
        content_length = 0
        try:
            while True:
                raw = stdout.readline()
                if not raw:
                    return None  # EOF — server exited
                stripped = raw.strip()
                if not stripped:
                    break  # blank line ends LSP headers
                if stripped.lower().startswith(b"content-length:"):
                    content_length = int(stripped.split(b":", 1)[1].strip())
            if not content_length:
                return {}
            body = stdout.read(content_length)
            return json.loads(body) if body else {}
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("%s read error: %s", self._name, exc)
            return None

    def _read_one(self, timeout: float = 30.0) -> dict | None:  # type: ignore[type-arg]
        if self._proc.stdout is None or self._proc.poll() is not None:
            return None
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not ready:
            logger.warning("%s timed out after %.0fs", self._name, timeout)
            return None
        return self._read_frame()

    def _reply_server_request(self, mid: int, method: str) -> None:
        """
        Answer a server→client request so the load handshake proceeds.

        csharp-ls issues ``workspace/configuration``,
        ``client/registerCapability`` and ``window/workDoneProgress/create``
        during startup. Replying with a benign result (``null`` / a list of
        ``null`` configs) keeps it moving; anything genuinely unknown gets a
        MethodNotFound error.
        """
        if method == "workspace/configuration":
            self._write({"jsonrpc": "2.0", "id": mid, "result": [None]})
        elif method in (
            "client/registerCapability",
            "client/unregisterCapability",
            "window/workDoneProgress/create",
            "workspace/semanticTokens/refresh",
        ):
            self._write({"jsonrpc": "2.0", "id": mid, "result": None})
        else:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "error": {"code": -32601, "message": "Method not found"},
                }
            )

    def _wait_for_ready(self, budget: float = 60.0) -> None:
        """
        Drain notifications until the workspace-load progress ends.

        Roslyn reports solution/project loading via ``$/progress`` with an
        ``end`` value. Waiting for it once up front means every subsequent
        definition query sees a fully-loaded compilation. Falls through on
        timeout so a slow load degrades to best-effort rather than hanging —
        the resolver's circuit breaker then abandons a server that turns out
        never to answer instead of paying the read timeout on every root.
        """
        if self._proc.stdout is None or self._proc.poll() is not None:
            return
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            ready, _, _ = select.select(
                [self._proc.stdout], [], [], deadline - time.monotonic()
            )
            if not ready:
                return  # no signal within budget — proceed best-effort
            msg = self._read_frame()
            if msg is None:
                return  # EOF
            method = msg.get("method")
            mid = msg.get("id")
            if method and mid is not None:
                self._reply_server_request(mid, method)
            elif method == "$/progress":
                value = msg.get("params", {}).get("value", {})
                if value.get("kind") == "end":
                    return

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
                    self._reply_server_request(msg_id, msg["method"])
                continue
            if msg_id == expected_id:
                return msg
        logger.warning(
            "%s did not respond to request %d", self._name, expected_id
        )
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
                    "window": {"workDoneProgress": True},
                },
                "workspaceFolders": [
                    {"uri": project_root.as_uri(), "name": project_root.name},
                ],
            },
            timeout=60.0,
        )
        if resp is not None:
            self._notify("initialized", {})
            self._wait_for_ready()

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
                        "languageId": "csharp",
                        "version": 1,
                        "text": text,
                    },
                },
            )
        return uri

    def _build_open_messages(self, files: list[Path]) -> list[dict]:  # type: ignore[type-arg]
        """Build (don't send) a ``didOpen`` for every not-yet-opened file."""
        msgs: list[dict] = []  # type: ignore[type-arg]
        for file in files:
            uri = file.as_uri()
            if uri in self._opened_uris:
                continue
            self._opened_uris.add(uri)
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            msgs.append(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "csharp",
                            "version": 1,
                            "text": text,
                        },
                    },
                }
            )
        return msgs

    def _write_all(self, msgs: list[dict]) -> None:  # type: ignore[type-arg]
        for msg in msgs:
            self._write(msg)

    def _drain_while_writing(
        self, writer: threading.Thread, budget: float = 120.0
    ) -> None:
        """Discard server notifications while *writer* is still sending."""
        if self._proc.stdout is None or self._proc.poll() is not None:
            return
        deadline = time.monotonic() + budget
        while writer.is_alive() and time.monotonic() < deadline:
            remaining = min(deadline - time.monotonic(), 0.5)
            ready, _, _ = select.select([self._proc.stdout], [], [], remaining)
            if not ready:
                continue
            msg = self._read_frame()
            if msg is None:
                return  # EOF
            mid = msg.get("id")
            if "method" in msg and mid is not None:
                self._reply_server_request(mid, msg["method"])

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def definition(self, file: Path, line: int, col: int) -> dict | None:  # type: ignore[type-arg]
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
        return self._first_location(resp.get("result"))

    @staticmethod
    def _first_location(result: object) -> dict | None:  # type: ignore[type-arg]
        """Reduce an LSP definition result to a single Location or None."""
        if isinstance(result, list):
            result = result[0] if result else None
        return result if isinstance(result, dict) else None

    def definition_batch(self, queries: list[Query]) -> list[dict | None]:  # type: ignore[type-arg]
        """
        Resolve many positions in one pipelined exchange.

        Two phases, each writing from a writer thread while the main thread
        reads concurrently so a full stdin/stdout pipe cannot deadlock: (1)
        ``didOpen`` every file, draining as it goes, then (2) send every
        ``textDocument/definition`` request up front and collect responses by
        JSON-RPC id. Order is preserved; unanswered positions stay ``None``.
        """
        if not queries:
            return []
        results: list[dict | None] = [None] * len(queries)
        if self._proc.poll() is not None:
            self.responsive = False
            return results
        open_msgs = self._build_open_messages([f for (f, _l, _c) in queries])
        if open_msgs:
            opener = threading.Thread(
                target=self._write_all, args=(open_msgs,), daemon=True
            )
            opener.start()
            self._drain_while_writing(opener)
            opener.join(timeout=5)
            if self._proc.poll() is not None:
                self.responsive = False
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

        writer = threading.Thread(
            target=self._write_all, args=(reqs,), daemon=True
        )
        writer.start()
        got = 0
        while got < len(queries):
            msg = self._read_one(timeout=self._query_timeout)
            if msg is None:
                break
            mid = msg.get("id")
            if "method" in msg:
                if mid is not None:
                    self._reply_server_request(mid, msg["method"])
                continue
            idx = id2idx.get(mid) if mid is not None else None
            if idx is not None:
                results[idx] = self._first_location(msg.get("result"))
                got += 1
        writer.join(timeout=5)
        # A responsive server answers every request (with a location or null);
        # a short read means it stopped answering — signal the breaker.
        self.responsive = got == len(queries)
        return results

    def references(self, file: Path, line: int, col: int) -> list[dict]:  # type: ignore[type-arg]
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
        if self._proc.poll() is None:
            try:
                self._request("shutdown", None)
                self._notify("exit", None)
                self._proc.wait(timeout=5)
            except Exception:
                with contextlib.suppress(Exception):
                    self._proc.kill()
        # Close the pipes so a dead process's buffered stdin is not flushed at
        # GC time — that surfaces as a stray "Exception ignored in
        # <BufferedWriter> ... BrokenPipeError" on stderr.
        for stream in (self._proc.stdin, self._proc.stdout):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.shutdown()


class CsharpLspResolver(SymbolResolver):
    """
    Resolve C# symbols via a ``csharp-ls`` subprocess.

    csharp-ls is a Roslyn-based LSP server distributed as a .NET global tool.
    ``textDocument/definition`` and ``textDocument/references`` are the only
    capabilities this resolver uses. Point ``$GRAPHLENS_CSHARP_LS`` at the
    binary, or have ``csharp-ls`` on ``PATH``.

    Spawns one server per :meth:`prepare` call via :class:`_CsharpLspClient`.
    If the server cannot be started, :meth:`prepare` logs a warning and all
    queries return ``None``/``[]`` — the structural graph is still produced.
    ``infer_type_at`` always returns ``None``.

    A circuit breaker guards wall-clock: if the server fails to answer a batch
    in full (a workspace that never finished loading, a crash), the resolver
    stops querying it for the rest of the run and :meth:`status` reports
    :data:`ResolverStatus.DEGRADED`, so one slow root cannot make every
    subsequent root pay the read timeout.
    """

    _engine = "csharp-ls"

    def __init__(self) -> None:
        self._client: _CsharpLspClient | None = None
        self._root: Path | None = None
        # Tripped when the server proves unresponsive mid-run; once set, later
        # batches short-circuit instead of each paying the read timeout.
        self._degraded = False

    def _spawn_argv(self) -> list[str]:
        binary = (
            os.environ.get("GRAPHLENS_CSHARP_LS")
            or shutil.which("csharp-ls")
            or "csharp-ls"
        )
        return [binary]

    def prepare(self, project_root: Path, files: list[Path]) -> None:  # noqa: ARG002
        self._shutdown_client()
        self._root = project_root
        self._degraded = False
        try:
            self._client = _CsharpLspClient(
                project_root, self._spawn_argv(), name=self._engine
            )
        except Exception:
            logger.warning(
                "Failed to start %s for %s", self._engine, project_root
            )
            self._client = None

    def _shutdown_client(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.shutdown()
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
        Resolve every occurrence in one pipelined LSP exchange.

        Overrides the per-query default: the resolution pass issues one query
        per occurrence, so batching writes them all up front and reads
        responses by id, collapsing N round-trips to the server's throughput.

        Circuit breaker: once the server has failed to answer a batch in full
        (a workspace that never finished loading, a crashed process), every
        later batch short-circuits to ``None`` so a multi-root project does not
        pay the read timeout once per root.
        """
        if self._client is None or self._degraded:
            return [None] * len(queries)
        try:
            locs = self._client.definition_batch(queries)
        except Exception:
            self._degraded = True
            return [None] * len(queries)
        if not self._client.responsive:
            self._degraded = True
        return [
            self._loc_to_ref(loc) if loc is not None else None for loc in locs
        ]

    def infer_type_at(
        self, file: Path, line: int, col: int  # noqa: ARG002
    ) -> ResolvedRef | None:
        return None

    def status(self) -> ResolverStatus:
        if self._client is None:
            return ResolverStatus.UNAVAILABLE
        if self._degraded:
            return ResolverStatus.DEGRADED
        return ResolverStatus.OK

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
            # Resolved into a compiled assembly (csharp:/ metadata URI); we
            # cannot tell BCL from NuGet from the location alone.
            return "unknown"
        parts = file_path.parts
        if ".nuget" in parts or "packages" in parts:
            return "third_party"
        if self._root is not None:
            with contextlib.suppress(ValueError):
                file_path.relative_to(self._root)
                return "internal"
        return "unknown"

    def __del__(self) -> None:
        self._shutdown_client()
