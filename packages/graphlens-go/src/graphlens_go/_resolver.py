"""Go symbol resolvers: a gopls LSP backend and a structure-only fallback."""

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

logger = logging.getLogger("graphlens_go")

# Consecutive path parts that mark Go's downloaded-module cache.
_MOD_CACHE = ("pkg", "mod")


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


def _in_mod_cache(parts: tuple[str, ...]) -> bool:
    """Return True if ``parts`` contains the ``pkg/mod`` cache segment."""
    return any(
        parts[i : i + 2] == _MOD_CACHE for i in range(len(parts) - 1)
    )


def _loc_uri_and_start(loc: dict) -> tuple[str, dict]:  # type: ignore[type-arg]
    """
    Extract (uri, start) from an LSP Location or LocationLink.

    gopls may return a ``LocationLink`` (``targetUri`` /
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
        # Serialize stdin writes: the pipelined batch runs a writer thread
        # while the main thread may still write MethodNotFound replies, so
        # two threads can race on stdin and interleave a frame's bytes.
        self._write_lock = threading.Lock()
        self._initialize(project_root)

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
            # Let gopls finish analyzing the file before the first query.
            # Without this, a definition request sent immediately after
            # didOpen blocks on background analysis and can hit the timeout,
            # silently yielding no edges.
            self._drain_for_diagnostics(uri)
        return uri

    def _drain_for_diagnostics(self, uri: str, timeout: float = 10.0) -> None:
        """Drain until publishDiagnostics for *uri* arrives (or timeout)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self._read_one(timeout=min(deadline - time.monotonic(), 1.0))
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
        Open many files at once: send every ``didOpen`` first, then drain.

        Batching the opens lets gopls analyze the whole set in parallel and
        collapses N serial per-file diagnostic waits into one — the bulk of
        the win on file-heavy modules.
        """
        pending: set[str] = set()
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
                        "languageId": "go",
                        "version": 1,
                        "text": text,
                    },
                },
            )
            pending.add(uri)
        if pending:
            self._drain_for_diagnostics_multi(pending)

    def _drain_for_diagnostics_multi(
        self, uris: set[str], timeout: float = 60.0
    ) -> None:
        """Wait for ``publishDiagnostics`` from every uri (or until time)."""
        deadline = time.monotonic() + timeout
        remaining = set(uris)
        while remaining and time.monotonic() < deadline:
            if self._proc.poll() is not None:
                return
            msg = self._read_one(timeout=min(deadline - time.monotonic(), 2.0))
            if msg is None:
                # A quiet slice during workspace load is expected; keep
                # waiting until the deadline instead of bailing on the first
                # gap (the per-file drain bailed here and fired queries
                # before analysis finished, silently yielding no edges).
                continue
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
            elif method == "textDocument/publishDiagnostics":
                remaining.discard(msg.get("params", {}).get("uri"))

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
            timeout=60.0,
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
        return (
            ResolverStatus.OK
            if self._client is not None
            else ResolverStatus.UNAVAILABLE
        )

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
        # gopls may return a canonicalized (symlink-resolved) path; resolve
        # both sides so a symlinked workspace still classifies internal
        # definitions as "internal" rather than mislabeling them "unknown".
        resolved = _safe_resolve(file_path)
        if _in_mod_cache(resolved.parts):
            return "third_party"
        if self._root is not None and _within(resolved, self._root):
            return "internal"
        if self._goroot is not None and _within(resolved, self._goroot):
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
