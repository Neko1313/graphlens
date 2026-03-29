"""Dependency file parsers for TypeScript / Node.js projects."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from graphlens.contracts import DependencyFileParser, normalize_pkg_name

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Package.json parser
# ---------------------------------------------------------------------------


class PackageJsonParser(DependencyFileParser):
    """
    Reads declared dependencies from ``package.json``.

    Includes ``dependencies``, ``devDependencies``, ``peerDependencies``,
    and ``optionalDependencies`` so that test-only and peer imports are
    classified as ``third_party`` rather than ``unknown``.
    """

    def can_parse(self, project_root: Path) -> bool:
        return (project_root / "package.json").exists()

    def parse(self, project_root: Path) -> frozenset[str]:
        path = project_root / "package.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return frozenset()

        names: set[str] = set()
        for section in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            for dep in data.get(section, {}):
                n = normalize_pkg_name(dep)
                if n:
                    names.add(n)
        return frozenset(names)


# ---------------------------------------------------------------------------
# Default parser list
# ---------------------------------------------------------------------------

TYPESCRIPT_DEFAULT_DEP_PARSERS: list[DependencyFileParser] = [
    PackageJsonParser(),
]


# ---------------------------------------------------------------------------
# Node.js stdlib / built-in module names
# ---------------------------------------------------------------------------

def get_stdlib_names() -> frozenset[str]:
    """
    Return top-level module names that ship with Node.js.

    These are the importable names callers use, without the ``node:``
    scheme prefix (e.g. ``"fs"``, not ``"node:fs"``).  The adapter strips
    ``node:`` prefixes from import paths before calling
    ``ImportClassifier.classify()``, so plain names are sufficient.
    """
    return frozenset({
        # Core Node.js built-in modules (stable API)
        "assert",
        "async_hooks",
        "buffer",
        "child_process",
        "cluster",
        "console",
        "constants",
        "crypto",
        "dgram",
        "diagnostics_channel",
        "dns",
        "domain",
        "events",
        "fs",
        "http",
        "http2",
        "https",
        "inspector",
        "module",
        "net",
        "os",
        "path",
        "perf_hooks",
        "process",
        "punycode",
        "querystring",
        "readline",
        "repl",
        "stream",
        "string_decoder",
        "sys",
        "timers",
        "tls",
        "trace_events",
        "tty",
        "url",
        "util",
        "v8",
        "vm",
        "wasi",
        "worker_threads",
        "zlib",
    })
