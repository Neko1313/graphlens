"""
C# symbol resolvers.

Two resolvers, the same tradeoff as the Rust adapter's LSP-vs-SCIP pair:

``CsharpScipResolver`` (the default) runs ``scip-dotnet index`` — a Roslyn-
based *batch* SCIP indexer (Apache-2.0, Sourcegraph) — once per
:meth:`~CsharpScipResolver.prepare` call, then answers every query from an
in-memory index. No live workspace, no per-query round-trip, no read timeout
to go quiet on: the whole point of the batch shape. On the reference
``dotnet/eShop`` solution (24 projects, ~42k occurrences) this produces a
complete index with real cross-project resolution in well under a minute,
where the live-LSP path below needed a circuit breaker to avoid multi-minute
stalls and still left roughly half the occurrences unresolved.

``CsharpLspResolver`` drives a ``csharp-ls`` subprocess — Razzmatazz's
Roslyn-based C# language server, installable as a .NET global tool
(``dotnet tool install --global csharp-ls``) — over stdio via
:class:`_CsharpLspClient`. It uses ``textDocument/definition`` and
``textDocument/references``; both come from Roslyn's semantic model, so the
resolved definitions are type-aware (correct method overload, base type, field
declaration) rather than name-matched. Kept as an explicit alternative (inject
it via ``CsharpAdapter(resolver=CsharpLspResolver())``) for callers who want
live queries against an already-running workspace rather than a batch index.

Point ``$GRAPHLENS_CSHARP_LS`` at the binary, or have ``csharp-ls`` on
``PATH``. csharp-ls loads the solution/project via Roslyn on ``initialize``;
the client waits for the workspace-load progress to end before issuing
queries. When the binary (or the .NET runtime it needs) is absent it degrades
automatically: :meth:`CsharpLspResolver.status` reports
:data:`ResolverStatus.UNAVAILABLE` and every query returns ``None``/``[]``, so
the structural graph is still produced with only the type-aware edges dropped.

Both resolvers never raise: every error returns ``None``/``[]``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import select
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import IO
from urllib.parse import unquote

from graphlens.contracts import Occurrence, Query, ResolvedRef, SymbolResolver
from graphlens.status import ResolverStatus

from graphlens_csharp._scip import SCIP_ROLE_DEFINITION, iter_documents

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


# ---------------------------------------------------------------------------
# CsharpScipResolver — batch SCIP index via scip-dotnet
# ---------------------------------------------------------------------------

# Wall-clock ceiling for one ``scip-dotnet index`` run. On the reference
# dotnet/eShop solution (24 projects) this finishes in well under a minute;
# the cap only guards a pathological hang (e.g. a restore stuck on a network
# fetch).
_SCIP_TIMEOUT_S = 1800.0

# A scip-dotnet SCIP symbol needs at least <scheme> <manager> <package>
# <version> to classify its origin.
_SCIP_SYMBOL_MIN_PARTS = 4


def _find_solution(project_root: Path) -> Path | None:
    """
    Return a ``.slnx``/``.sln`` directly under ``project_root``, if any.

    Not recursive: only the top level is checked, matching the scope of
    scip-dotnet's own auto-discovery. ``.slnx`` is preferred when both exist
    since it is the format the reference ``dotnet/eShop`` solution ships
    (and the newer of the two); ties within one extension resolve
    alphabetically for determinism.
    """
    for pattern in ("*.slnx", "*.sln"):
        matches = sorted(project_root.glob(pattern))
        if matches:
            return matches[0]
    return None


def _scip_index_args(project_root: Path) -> list[str]:
    """
    Build the positional/flag args for ``scip-dotnet index`` on this root.

    Split out of :meth:`CsharpScipResolver._run_scip` (which is excluded
    from coverage as a subprocess boundary) specifically so this branching
    is unit-testable on its own: it is the exact logic that caused a real
    resolution failure once already (bare ``--working-directory`` silently
    exits 1 on a directory holding many projects plus a covering solution —
    see :class:`CsharpScipResolver`'s docstring), so a regression here
    should fail a fast unit test rather than only surface via a live
    benchmark run.
    """
    solution = _find_solution(project_root)
    if solution is not None:
        return [solution.name]
    return ["--working-directory", str(project_root)]


def _scip_symbol_origin(symbol: str) -> str:
    """
    Classify an external SCIP symbol: ``stdlib``/``third_party``/``unknown``.

    A scip-dotnet symbol reads ``scip-dotnet nuget <package> <version>
    <descriptors>``. ``<package>`` is literally ``.`` for a symbol declared
    inside the indexed solution itself — such a symbol only reaches this
    function when it was *not* found in :attr:`CsharpScipResolver._defs`
    (its defining file was outside the indexed set, e.g. a project that
    failed to restore), which is a genuine miss rather than a real origin, so
    it classifies as ``unknown``. ``System`` / ``System.*`` is the BCL,
    matching the same rule ``_deps.get_stdlib_names`` uses for the import
    classifier; every other package (including ``Microsoft.*``, which mostly
    ships as independent NuGet packages) is ``third_party``.
    """
    parts = symbol.split(" ", 4)
    if len(parts) < _SCIP_SYMBOL_MIN_PARTS or parts[1] != "nuget":
        return "unknown"
    package = parts[2]
    if package == ".":
        return "unknown"
    if package == "System" or package.startswith("System."):
        return "stdlib"
    return "third_party"


class CsharpScipResolver(SymbolResolver):
    """
    Resolve C# symbols from a ``scip-dotnet index`` batch index.

    Instead of driving an interactive Roslyn LSP server (which pays a per-
    query round-trip and can go quiet mid-workspace-load on a large or
    partially-broken solution — see :class:`CsharpLspResolver`),
    :meth:`prepare` runs ``scip-dotnet index`` once to write a static SCIP
    index, parses it, and answers every query from in-memory lookup tables.

    Requires ``scip-dotnet`` on ``PATH`` (install:
    ``dotnet tool install --global scip-dotnet``; point
    ``$GRAPHLENS_SCIP_DOTNET`` at the binary to override). If the batch run
    fails or the binary is missing, every query returns ``None``/``[]`` so
    the structural graph still stands and :meth:`status` reports
    ``UNAVAILABLE``.

    scip-dotnet's own auto-discovery — ``--working-directory`` pointed at
    ``project_root`` with no positional argument — does not handle a
    directory holding many projects plus a covering solution: confirmed
    against the 24-project ``dotnet/eShop`` solution, where it exits 1 with
    no index and no stderr output. Passing the solution file explicitly
    avoids that: it routes through a single
    ``MSBuildWorkspace.OpenSolutionAsync`` call instead, which is what the
    eShop validation actually exercised end to end. So :meth:`prepare` looks
    for one ``*.slnx``/``*.sln`` directly under ``project_root`` (not
    recursively) and passes it by name when found; otherwise it falls back
    to the bare ``--working-directory`` form (scip-dotnet's documented
    default for a single project, not independently verified here).

    Never pass more than one project path: unlike csharp-ls, scip-dotnet
    does not guard against being handed the same project twice, so two
    paths that reference each other (a project pulled in transitively by
    one argument's ``ProjectReference`` graph, then named again as its own
    argument) crash the underlying Roslyn call with an unhandled exception.
    That failure is caught the same as any other and degrades to
    ``UNAVAILABLE`` rather than propagating.

    All methods return ``None``/``[]`` on any error — never raise.
    ``infer_type_at`` always returns ``None``.
    """

    _engine = "scip-dotnet"

    def __init__(self) -> None:
        self._root: Path | None = None
        self._status = ResolverStatus.UNAVAILABLE
        # relative_path -> {(line0, col0): symbol} for every occurrence.
        self._by_doc: dict[str, dict[tuple[int, int], str]] = {}
        # global symbol -> (relative_path, line0, col0) of its definition.
        self._defs: dict[str, tuple[str, int, int]] = {}
        # relative_path -> {document-scoped "local …" symbol: (line0, col0)}.
        self._local_defs: dict[str, dict[str, tuple[int, int]]] = {}

    def _spawn_argv(self) -> list[str]:
        binary = (
            os.environ.get("GRAPHLENS_SCIP_DOTNET")
            or shutil.which("scip-dotnet")
            or "scip-dotnet"
        )
        return [binary]

    def prepare(self, project_root: Path, files: list[Path]) -> None:  # noqa: ARG002
        self._root = project_root.resolve()
        self._by_doc = {}
        self._defs = {}
        self._local_defs = {}
        self._status = ResolverStatus.UNAVAILABLE
        try:
            data, returncode = self._run_scip(project_root)
            if data is None:
                return
            self._ingest(data)
            if not self._by_doc:
                self._status = ResolverStatus.DEGRADED
            elif returncode != 0:
                # scip-dotnet errored mid-run (e.g. a project failed to
                # restore) but left a partial index. Report DEGRADED rather
                # than OK so strict mode won't trust a silently incomplete
                # graph — the LSP resolver signals the analogous case the
                # same way.
                self._status = ResolverStatus.DEGRADED
            else:
                self._status = ResolverStatus.OK
        except Exception:
            logger.warning("scip-dotnet index failed for %s", project_root)
            self._status = ResolverStatus.UNAVAILABLE

    def _run_scip(  # pragma: no cover - subprocess
        self, project_root: Path
    ) -> tuple[bytes | None, int | None]:
        """Run ``scip-dotnet index``; return ``(index bytes, exit code)``."""
        argv = self._spawn_argv()
        stdout = tempfile.TemporaryFile()  # noqa: SIM115
        stderr = tempfile.TemporaryFile()  # noqa: SIM115
        fd, out_name = tempfile.mkstemp(suffix=".scip")
        os.close(fd)  # we only need the path; scip-dotnet writes the file
        out_path = Path(out_name)
        target_args = _scip_index_args(project_root)
        try:
            proc = subprocess.run(
                [*argv, "index", *target_args, "--output", str(out_path)],
                cwd=str(project_root),
                stdout=stdout,
                stderr=stderr,
                timeout=_SCIP_TIMEOUT_S,
                check=False,
            )
            if out_path.is_file() and out_path.stat().st_size > 0:
                return out_path.read_bytes(), proc.returncode
            self._log_scip_failure(proc.returncode, stdout, stderr)
            return None, proc.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("scip-dotnet index did not complete: %s", exc)
            return None, None
        finally:
            with contextlib.suppress(Exception):
                stdout.close()
            with contextlib.suppress(Exception):
                stderr.close()
            with contextlib.suppress(OSError):
                out_path.unlink()

    @staticmethod
    def _log_scip_failure(  # pragma: no cover - subprocess
        returncode: int | None, stdout: IO[bytes], stderr: IO[bytes]
    ) -> None:
        """Log the exit code and stdout/stderr tails when no index came out."""

        def _tail(stream: IO[bytes]) -> str:
            with contextlib.suppress(Exception):
                stream.flush()
                stream.seek(0)
                text = stream.read().decode("utf-8", errors="replace")
                return text[-2000:].strip()
            return ""

        logger.warning(
            "scip-dotnet index produced no index (exit %s); the solution "
            "likely failed to load. stdout tail:\n%s\nstderr tail:\n%s",
            returncode,
            _tail(stdout) or "<empty>",
            _tail(stderr) or "<empty>",
        )

    def _ingest(self, data: bytes) -> None:
        """Fold a SCIP index into the by-document and definition tables."""
        pool: dict[str, str] = {}  # intern symbols: many occurrences share one
        for rel, occurrences in iter_documents(data):
            doc_map: dict[tuple[int, int], str] = {}
            for occ in occurrences:
                if not occ.symbol:
                    continue
                symbol = pool.setdefault(occ.symbol, occ.symbol)
                key = (occ.start_line, occ.start_col)
                doc_map[key] = symbol
                if occ.roles & SCIP_ROLE_DEFINITION:
                    if symbol.startswith("local "):
                        self._local_defs.setdefault(rel, {})[symbol] = key
                    else:
                        self._defs.setdefault(
                            symbol, (rel, occ.start_line, occ.start_col)
                        )
            if doc_map:
                self._by_doc[rel] = doc_map

    def _rel(self, file: Path) -> str:
        """
        Map an absolute file to its index-relative form (root-relative).

        SCIP ``relative_path`` always uses forward slashes, so normalise with
        ``as_posix()`` — otherwise ``_by_doc`` lookups would miss on Windows.
        """
        if self._root is None:  # pragma: no cover - guarded by callers
            return str(file)
        try:
            return file.resolve().relative_to(self._root).as_posix()
        except (ValueError, OSError):
            return str(file)

    def _symbol_at_rel(self, rel: str, line: int, col: int) -> str | None:
        """Return the SCIP symbol at (line, col) in the document *rel*."""
        doc_map = self._by_doc.get(rel)
        if doc_map is None:
            return None
        return doc_map.get((line - 1, col - 1))

    def _symbol_at(self, file: Path, line: int, col: int) -> str | None:
        """Return the SCIP symbol whose occurrence starts at (line, col)."""
        return self._symbol_at_rel(self._rel(file), line, col)

    def definition_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        root = self._root
        if root is None:
            return None
        rel = self._rel(file)  # one resolve() per query, reused below
        symbol = self._symbol_at_rel(rel, line, col)
        if symbol is None:
            return None
        return self._symbol_to_ref(symbol, rel, root)

    def _symbol_to_ref(
        self, symbol: str, doc_rel: str, root: Path
    ) -> ResolvedRef | None:
        """Resolve a symbol to its definition, or to an external ref."""
        if symbol.startswith("local "):
            loc = self._local_defs.get(doc_rel, {}).get(symbol)
            if loc is None:
                return None
            return ResolvedRef(
                full_name="",
                file_path=root / doc_rel,
                line=loc[0] + 1,
                col=loc[1] + 1,
                kind="",
                origin="internal",
            )
        target = self._defs.get(symbol)
        if target is not None:
            rel, line0, col0 = target
            return ResolvedRef(
                full_name="",
                file_path=root / rel,
                line=line0 + 1,
                col=col0 + 1,
                kind="",
                origin="internal",
            )
        return ResolvedRef(
            full_name=symbol,
            file_path=None,
            line=0,
            col=0,
            kind="",
            origin=_scip_symbol_origin(symbol),
        )

    def resolve_all(self, queries: list[Query]) -> list[ResolvedRef | None]:
        if self._root is None:
            return [None] * len(queries)
        try:
            return [
                self.definition_at(file, line, col)
                for (file, line, col) in queries
            ]
        except Exception:  # pragma: no cover - lookups don't raise
            return [None] * len(queries)

    def infer_type_at(
        self, file: Path, line: int, col: int  # noqa: ARG002
    ) -> ResolvedRef | None:
        return None

    def references_to(
        self, file: Path, line: int, col: int
    ) -> list[Occurrence]:
        root = self._root
        if root is None:
            return []
        symbol = self._symbol_at(file, line, col)
        if symbol is None or symbol.startswith("local "):
            return []
        out: list[Occurrence] = []
        for rel, doc_map in self._by_doc.items():
            for (line0, col0), sym in doc_map.items():
                if sym != symbol:
                    continue
                is_def = self._defs.get(symbol) == (rel, line0, col0)
                if is_def:
                    continue  # exclude the declaration, like the LSP path
                out.append(
                    Occurrence(
                        file_path=root / rel,
                        line=line0 + 1,
                        col=col0 + 1,
                        is_definition=False,
                        access="unknown",
                    )
                )
        return out

    def status(self) -> ResolverStatus:
        return self._status
