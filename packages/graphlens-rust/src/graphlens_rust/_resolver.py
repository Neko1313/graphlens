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
from urllib.parse import unquote

from graphlens.contracts import Occurrence, Query, ResolvedRef, SymbolResolver
from graphlens.status import ResolverStatus

logger = logging.getLogger("graphlens_rust")

# Consecutive path parts marking Cargo's downloaded-crate cache.
_REGISTRY = ("registry", "src")

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


def _resolve_ra_binary() -> str:
    """
    Resolve the real rust-analyzer binary, bypassing the rustup proxy.

    ``rust-analyzer`` on PATH is usually a rustup *proxy* that honours a
    project's ``rust-toolchain.toml``. When the pinned toolchain lacks the
    rust-analyzer component (e.g. ruff pins ``1.96`` without it), the proxy
    exits with ``Unknown binary 'rust-analyzer'`` and resolution silently
    yields nothing. Resolving the concrete binary for a toolchain that *does*
    have the component — via ``rustup which`` from a neutral directory (no
    ``rust-toolchain.toml``) — and spawning it directly sidesteps that switch.
    Falls back to the PATH entry when rustup is absent (a standalone install).
    """
    direct = shutil.which("rust-analyzer")
    rustup = shutil.which("rustup")
    if rustup is not None:
        with contextlib.suppress(Exception):
            env = {
                k: v for k, v in os.environ.items() if k != "RUSTUP_TOOLCHAIN"
            }
            out = subprocess.run(
                [rustup, "which", "rust-analyzer"],
                cwd=tempfile.gettempdir(),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                env=env,
            )
            path = out.stdout.strip()
            if out.returncode == 0 and path and Path(path).is_file():
                return path
    return direct or "rust-analyzer"


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
        ra_bin = _resolve_ra_binary()
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
