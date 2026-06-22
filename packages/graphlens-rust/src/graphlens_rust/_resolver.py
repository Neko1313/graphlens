"""Rust resolvers: rust-analyzer LSP backend + structure-only fallback."""

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

from graphlens_rust._scip import SCIP_ROLE_DEFINITION, iter_documents

logger = logging.getLogger("graphlens_rust")

# Consecutive path parts marking Cargo's downloaded-crate cache.
_REGISTRY = ("registry", "src")

# Wall-clock ceiling for one ``rust-analyzer scip`` batch run. Large workspaces
# (e.g. ruff) finish well under this; the cap only guards a pathological hang.
_SCIP_TIMEOUT_S = 1800.0

# Cargo "package" names in a SCIP symbol that denote the Rust standard
# distribution rather than a third-party crate. A SCIP symbol reads
# ``<scheme> cargo <package> <version> <descriptors>``.
_STD_PACKAGES = frozenset({"std", "core", "alloc", "proc_macro", "test"})

# A cargo SCIP symbol needs at least <scheme> <manager> <package> <version>.
_SCIP_SYMBOL_MIN_PARTS = 4

# If rust-analyzer emits no $/progress within this many seconds, assume it does
# not report progress and treat the workspace as ready (see _wait_until_ready).
_NO_PROGRESS_GRACE_S = 10.0

# rust-analyzer settings that trade work we never use for a lighter, more
# robust workspace load. We never save files, so cargo check (checkOnSave) is
# pure overhead; build-script and cache-priming work can dominate (or fail
# outright, e.g. needing network/a compiler) on large workspaces such as ruff
# without improving definition resolution. proc-macro expansion stays ENABLED
# because disabling it measurably lowers resolution on macro-heavy crates
# (e.g. axum's derive/attribute macros).
_RA_INIT_OPTIONS: dict = {  # type: ignore[type-arg]
    "checkOnSave": False,
    "cargo": {"buildScripts": {"enable": False}},
    "cachePriming": {"enable": False},
    "procMacro": {"enable": True},
}


def _rustup_which(rustup: str, cwd: str) -> str | None:
    """
    Return the concrete rust-analyzer path rustup resolves from *cwd*.

    None if rustup errors or the resolved binary does not exist (e.g. a pinned
    toolchain that lacks the component).
    """
    with contextlib.suppress(Exception):
        env = {
            k: v for k, v in os.environ.items() if k != "RUSTUP_TOOLCHAIN"
        }
        out = subprocess.run(
            [rustup, "which", "rust-analyzer"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
        path = out.stdout.strip()
        if out.returncode == 0 and path and Path(path).is_file():
            return path
    return None


def _resolve_ra_binary(project_root: Path) -> str:
    """
    Resolve the concrete rust-analyzer binary to spawn for *project_root*.

    ``rust-analyzer`` on PATH is usually a rustup *proxy* that honours a
    project's ``rust-toolchain.toml``. Two failure modes follow:

    1. The pinned toolchain *has* the component — we want that exact binary,
       because a build matching the project's toolchain analyses it correctly.
       So we first ask ``rustup which`` from ``project_root`` (honouring the
       pin) and spawn the concrete path it reports.
    2. The pinned toolchain *lacks* the component (e.g. ruff pins ``1.96``
       without rust-analyzer) — the proxy would exit with ``Unknown binary
       'rust-analyzer'`` and resolution would silently yield nothing. The
       project-rooted lookup returns None (no such file), so we fall back to
       the default toolchain's binary resolved from a neutral directory.

    Falls back to the PATH entry when rustup is absent (a standalone install).
    """
    rustup = shutil.which("rustup")
    if rustup is not None:
        return (
            _rustup_which(rustup, str(project_root))
            or _rustup_which(rustup, tempfile.gettempdir())
            or shutil.which("rust-analyzer")
            or "rust-analyzer"
        )
    return shutil.which("rust-analyzer") or "rust-analyzer"


def _uri_to_path(uri: str) -> Path | None:
    """Convert a ``file://`` URI to a ``Path``; None for other schemes."""
    if not uri.startswith("file://"):
        return None
    return Path(unquote(uri[7:]))


def _safe_resolve(path: Path) -> Path:
    """Best-effort ``Path.resolve()`` that never raises."""
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - platform dependent
        return path


def _within(path: Path, base: Path) -> bool:
    """Return True if *path* is inside *base*, comparing resolved forms."""
    try:
        path.relative_to(_safe_resolve(base))
    except ValueError:
        return False
    return True


def _in_cargo_registry(parts: tuple[str, ...]) -> bool:
    """Return True if ``parts`` contains the Cargo ``registry/src`` cache."""
    return any(
        parts[i : i + 2] == _REGISTRY for i in range(len(parts) - 1)
    )


def _in_rust_stdlib(parts: tuple[str, ...]) -> bool:
    """Return True if ``parts`` points inside the Rust std source tree."""
    return "rustlib" in parts


def _loc_uri_and_start(loc: dict) -> tuple[str, dict]:  # type: ignore[type-arg]
    """
    Extract (uri, start) from an LSP Location or LocationLink.

    rust-analyzer may return a ``LocationLink`` (``targetUri`` /
    ``targetSelectionRange``) rather than a plain ``Location`` (``uri`` /
    ``range``); normalize both so resolution binds to the real definition.
    """
    uri = loc.get("uri") or loc.get("targetUri") or ""
    rng = (
        loc.get("range")
        or loc.get("targetSelectionRange")
        or loc.get("targetRange")
        or {}
    )
    return uri, rng.get("start", {})


class _RustAnalyzerClient:  # pragma: no cover - subprocess transport
    """Minimal synchronous LSP JSON-RPC client for ``rust-analyzer``."""

    def __init__(self, project_root: Path) -> None:
        ra_bin = _resolve_ra_binary(project_root)
        # Capture stderr to a temp file (not DEVNULL) so a workspace that fails
        # to load — rust-analyzer exiting/panicking, e.g. on ruff — leaves a
        # diagnosable trail. A real file never blocks the child the way an
        # unread PIPE would.
        self._stderr = tempfile.TemporaryFile()  # noqa: SIM115
        self._crash_logged = False
        self._proc: subprocess.Popen = subprocess.Popen(  # type: ignore[type-arg]
            [ra_bin],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            cwd=str(project_root),
        )
        self._next_id = 0
        self._opened_uris: set[str] = set()
        # Serialize stdin writes: the pipelined batch runs a writer thread
        # while the main thread may still write MethodNotFound replies, so
        # two threads can race on stdin and interleave a frame's bytes.
        self._write_lock = threading.Lock()
        self._initialize(project_root)

    def _log_crash_if_dead(self) -> None:
        """If rust-analyzer exited, log its code and stderr tail once."""
        if self._crash_logged or self._proc.poll() is None:
            return
        self._crash_logged = True
        tail = ""
        try:
            self._stderr.flush()
            self._stderr.seek(0)
            data = self._stderr.read()
            if isinstance(data, bytes):
                tail = data.decode("utf-8", errors="replace")
            tail = tail[-2000:].strip()
        except OSError:
            tail = ""
        logger.warning(
            "rust-analyzer exited (code %s) before resolution completed; "
            "the workspace likely failed to load. stderr tail:\n%s",
            self._proc.returncode,
            tail or "<empty>",
        )

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

    def _read_one(
        self, timeout: float = 30.0, *, warn_on_timeout: bool = True
    ) -> dict | None:  # type: ignore[type-arg]
        if self._proc.stdout is None or self._proc.poll() is not None:
            return None
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not ready:
            if warn_on_timeout:
                logger.warning("rust-analyzer timed out after %.0fs", timeout)
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
            logger.debug("rust-analyzer read error: %s", exc)
            return None

    def _recv_response(
        self, expected_id: int, timeout: float = 30.0
    ) -> dict | None:  # type: ignore[type-arg]
        for _ in range(2000):
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
        logger.warning(
            "rust-analyzer did not respond to request %d", expected_id
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

    def _initialize(self, project_root: Path) -> None:
        resp = self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": project_root.as_uri(),
                # Lighten the workspace load (skip cargo check / build
                # scripts / cache priming) so large workspaces load faster and
                # are less likely to fail outright. See _RA_INIT_OPTIONS.
                "initializationOptions": _RA_INIT_OPTIONS,
                "capabilities": {
                    # Opt into $/progress so we can tell when the workspace has
                    # finished loading (see _wait_until_ready).
                    "window": {"workDoneProgress": True},
                    "textDocument": {
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                    },
                },
                "workspaceFolders": [
                    {"uri": project_root.as_uri(), "name": project_root.name},
                ],
            },
            timeout=90.0,
        )
        if resp is not None:
            self._notify("initialized", {})
            self._wait_until_ready()
        self._log_crash_if_dead()

    def _wait_until_ready(self, timeout: float = 240.0) -> None:
        """
        Block until rust-analyzer has finished loading the workspace.

        Cross-crate definitions only resolve once rust-analyzer has built the
        crate graph and primed its caches — completion is signalled by
        ``$/progress`` notifications, not per-file diagnostics. Returns as soon
        as every begun progress token has ended (the server is idle), or on
        timeout. ``window/workDoneProgress/create`` server requests are
        answered so the server does not stall.
        """
        deadline = time.monotonic() + timeout
        start = time.monotonic()
        active: set[str | int | None] = set()
        saw_progress = False
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                return
            msg = self._read_one(
                timeout=min(deadline - time.monotonic(), 2.0),
                warn_on_timeout=False,
            )
            if msg is None:
                if saw_progress and not active:
                    return  # every progress token ended → workspace ready
                if (
                    not saw_progress
                    and time.monotonic() - start > _NO_PROGRESS_GRACE_S
                ):
                    return  # server emits no progress; assume ready
                continue
            method = msg.get("method", "")
            msg_id = msg.get("id")
            if method == "window/workDoneProgress/create":
                if msg_id is not None:
                    self._write(
                        {"jsonrpc": "2.0", "id": msg_id, "result": None}
                    )
                continue
            if method == "$/progress":
                value = msg.get("params", {}).get("value", {})
                token = msg.get("params", {}).get("token")
                kind = value.get("kind")
                if kind == "begin":
                    active.add(token)
                    saw_progress = True
                elif kind == "end":
                    active.discard(token)
                continue
            if method and msg_id is not None:
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
                        "languageId": "rust",
                        "version": 1,
                        "text": text,
                    },
                },
            )
            # Let rust-analyzer finish analyzing the file before the first
            # query. Without this, a definition request sent immediately
            # after didOpen blocks on background indexing and can hit the
            # timeout, silently yielding no edges.
            self._drain_for_diagnostics(uri)
        return uri

    def _drain_for_diagnostics(self, uri: str, timeout: float = 10.0) -> None:
        """Drain until publishDiagnostics for *uri* arrives (or timeout)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._read_one(
                timeout=min(deadline - time.monotonic(), 1.0),
                warn_on_timeout=False,
            )
            if msg is None:
                break
            method = msg.get("method", "")
            msg_id = msg.get("id")
            if method and msg_id is not None:
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
            elif (
                method == "textDocument/publishDiagnostics"
                and msg.get("params", {}).get("uri") == uri
            ):
                return

    def open_files(self, files: list[Path]) -> None:
        """
        Open many files at once by sending every ``didOpen`` up front.

        No per-file diagnostic wait is needed: :meth:`_wait_until_ready`
        already blocked until the workspace finished loading, and
        rust-analyzer processes each ``didOpen`` before the definition
        requests that follow it (notifications/requests are handled in order).
        """
        for file in files:
            uri = file.as_uri()
            if uri in self._opened_uris:
                continue
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
                        "languageId": "rust",
                        "version": 1,
                        "text": text,
                    },
                },
            )

    @staticmethod
    def _normalize_result(result: object) -> dict | None:  # type: ignore[type-arg]
        """Reduce an LSP definition result to a single Location or None."""
        if not result:
            return None
        if isinstance(result, list):
            return result[0] if result else None
        return result  # type: ignore[return-value]

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
            timeout=90.0,
        )
        if resp is None:
            return None
        return self._normalize_result(resp.get("result"))

    def definition_batch(
        self, queries: list[Query]
    ) -> list[dict | None]:  # type: ignore[type-arg]
        """
        Resolve many positions in one pipelined exchange.

        Writes every ``textDocument/definition`` request up front (from a
        writer thread so a full stdin/stdout pipe can't deadlock against our
        reads), then collects responses by JSON-RPC id. Order is preserved;
        unanswered positions stay ``None``.
        """
        if not queries:
            return []
        self.open_files([f for (f, _l, _c) in queries])
        results: list[dict | None] = [None] * len(queries)
        if self._proc.poll() is not None:
            self._log_crash_if_dead()
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
                        "position": {"line": line - 1, "character": col - 1},
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
            msg = self._read_one(timeout=90.0)
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
                results[idx] = self._normalize_result(msg.get("result"))
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
            timeout=90.0,
        )
        if resp is None:
            return []
        result = resp.get("result")
        return result if isinstance(result, list) else []

    def shutdown(self) -> None:
        if self._proc.poll() is None:
            try:
                self._request("shutdown", None)
                self._notify("exit", None)
                self._proc.wait(timeout=5)
            except Exception:
                with contextlib.suppress(Exception):
                    self._proc.kill()
        with contextlib.suppress(Exception):
            self._stderr.close()

    def is_alive(self) -> bool:
        """Return True while the rust-analyzer process is still running."""
        return self._proc.poll() is None

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.shutdown()


class RustAnalyzerResolver(SymbolResolver):
    """
    Resolve Rust symbols via ``rust-analyzer`` (the official Rust LSP server).

    Spawns one ``rust-analyzer`` subprocess per :meth:`prepare` call. Files
    are opened lazily on the first query. Requires ``rust-analyzer`` in
    ``PATH``; if it is missing, :meth:`prepare` logs a warning and every
    query returns ``None``/``[]`` so the structural graph is still produced
    and :meth:`status` reports ``UNAVAILABLE``.

    All methods return ``None``/``[]`` on any error — never raise.
    ``infer_type_at`` always returns ``None``.
    """

    def __init__(self) -> None:
        self._client: _RustAnalyzerClient | None = None
        self._root: Path | None = None

    def prepare(self, project_root: Path, files: list[Path]) -> None:  # noqa: ARG002
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.shutdown()
            self._client = None
        self._root = project_root
        try:
            self._client = _RustAnalyzerClient(project_root)
        except Exception:
            logger.warning(
                "Failed to start rust-analyzer for %s", project_root
            )
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

    def resolve_all(
        self, queries: list[Query]
    ) -> list[ResolvedRef | None]:
        if self._client is None or not queries:
            return [None] * len(queries)
        try:
            locs = self._client.definition_batch(list(queries))
        except Exception:
            return [None] * len(queries)
        return [self._loc_to_ref(loc) if loc else None for loc in locs]

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
            uri, start = _loc_uri_and_start(loc)
            fp = _uri_to_path(uri)
            if fp is None:
                continue
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
        if self._client is None:
            return ResolverStatus.UNAVAILABLE
        # A client that started but whose process has since exited (e.g. a
        # workspace that failed to load) produced an incomplete graph — report
        # DEGRADED rather than OK so callers don't trust a half result.
        if not self._client.is_alive():
            return ResolverStatus.DEGRADED
        return ResolverStatus.OK

    def _loc_to_ref(self, loc: dict) -> ResolvedRef:  # type: ignore[type-arg]
        uri, start = _loc_uri_and_start(loc)
        fp = _uri_to_path(uri)
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
        # rust-analyzer may return a canonicalized (symlink-resolved) path;
        # resolve both sides so a symlinked workspace still classifies
        # internal definitions as "internal" rather than "unknown".
        resolved = _safe_resolve(file_path)
        parts = resolved.parts
        if _in_cargo_registry(parts):
            return "third_party"
        if _in_rust_stdlib(parts):
            return "stdlib"
        if self._root is not None and _within(resolved, self._root):
            return "internal"
        return "unknown"

    def __del__(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.shutdown()


def _scip_symbol_origin(symbol: str) -> str:
    """
    Classify an external SCIP symbol: ``stdlib``/``third_party``/``unknown``.

    The origin is read straight from the symbol's scheme
    (``<scheme> cargo <package> <version> <descriptors>``) — more robust than
    guessing from a definition file path, and available even when the
    definition lives in a crate whose source was never opened.
    """
    parts = symbol.split(" ", 4)
    if len(parts) < _SCIP_SYMBOL_MIN_PARTS or parts[1] != "cargo":
        return "unknown"
    return "stdlib" if parts[2] in _STD_PACKAGES else "third_party"


class RustScipResolver(SymbolResolver):
    """
    Resolve Rust symbols from a ``rust-analyzer scip`` batch index.

    Instead of driving an interactive ``rust-analyzer`` LSP server (which keeps
    the whole workspace's analysis state resident and, on large workspaces such
    as ruff, balloons to tens of GB and degrades), :meth:`prepare` runs
    ``rust-analyzer scip`` once to write a static SCIP index, parses it, and
    answers every query from in-memory lookup tables. On ruff this is roughly
    ``2 GB`` / ``70 s`` versus ``15 GB`` / ``250 s`` for the LSP path, and it
    produces a complete index rather than a 9%-resolved degraded one.

    Requires ``rust-analyzer`` (and Cargo) on ``PATH``; if the batch run fails
    or the binary is missing, every query returns ``None``/``[]`` so the
    structural graph still stands and :meth:`status` reports ``UNAVAILABLE``.

    All methods return ``None``/``[]`` on any error — never raise.
    ``infer_type_at`` always returns ``None``.
    """

    def __init__(self) -> None:
        self._root: Path | None = None
        self._status = ResolverStatus.UNAVAILABLE
        # relative_path -> {(line0, col0): symbol} for every occurrence.
        self._by_doc: dict[str, dict[tuple[int, int], str]] = {}
        # global symbol -> (relative_path, line0, col0) of its definition.
        self._defs: dict[str, tuple[str, int, int]] = {}
        # relative_path -> {document-scoped "local …" symbol: (line0, col0)}.
        self._local_defs: dict[str, dict[str, tuple[int, int]]] = {}

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
                # rust-analyzer errored mid-run (e.g. a crate failed to load)
                # but left a partial index. Report DEGRADED rather than OK so
                # strict mode won't trust a silently incomplete graph — the
                # LSP resolver signals the analogous case the same way.
                self._status = ResolverStatus.DEGRADED
            else:
                self._status = ResolverStatus.OK
        except Exception:
            logger.warning(
                "rust-analyzer scip failed for %s", project_root
            )
            self._status = ResolverStatus.UNAVAILABLE

    def _run_scip(  # pragma: no cover - subprocess
        self, project_root: Path
    ) -> tuple[bytes | None, int | None]:
        """Run ``rust-analyzer scip``; return ``(index bytes, exit code)``."""
        ra_bin = _resolve_ra_binary(project_root)
        stderr = tempfile.TemporaryFile()  # noqa: SIM115
        fd, out_name = tempfile.mkstemp(suffix=".scip")
        os.close(fd)  # we only need the path; rust-analyzer writes the file
        out_path = Path(out_name)
        try:
            proc = subprocess.run(
                [ra_bin, "scip", str(project_root), "--output", str(out_path)],
                cwd=str(project_root),
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                timeout=_SCIP_TIMEOUT_S,
                check=False,
            )
            if out_path.is_file() and out_path.stat().st_size > 0:
                return out_path.read_bytes(), proc.returncode
            self._log_scip_failure(proc.returncode, stderr)
            return None, proc.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("rust-analyzer scip did not complete: %s", exc)
            return None, None
        finally:
            with contextlib.suppress(Exception):
                stderr.close()
            with contextlib.suppress(OSError):
                out_path.unlink()

    @staticmethod
    def _log_scip_failure(  # pragma: no cover - subprocess
        returncode: int | None, stderr: IO[bytes]
    ) -> None:
        """Log the exit code and a stderr tail when no index was produced."""
        tail = ""
        with contextlib.suppress(Exception):
            stderr.flush()
            stderr.seek(0)
            tail = stderr.read().decode("utf-8", errors="replace")
            tail = tail[-2000:].strip()
        logger.warning(
            "rust-analyzer scip produced no index (exit %s); the workspace "
            "likely failed to load. stderr tail:\n%s",
            returncode,
            tail or "<empty>",
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

    def resolve_all(
        self, queries: list[Query]
    ) -> list[ResolvedRef | None]:
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


class RustResolver(SymbolResolver):
    """
    Structure-only Rust resolver that always degrades.

    Useful as an explicit fallback (and in tests) when a rust-analyzer
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
