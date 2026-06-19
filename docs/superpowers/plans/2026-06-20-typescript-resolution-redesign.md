# TypeScript Resolution Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the TypeScript adapter to parity with the merged Python redesign — calls/references/types resolved to real declaration nodes via the TypeScript Compiler API.

**Architecture:** tree-sitter extracts structure + occurrence roles (call/read/write/annotation/base) + spans; a `TsResolver` runs a bundled Node script (`ts_resolver.js`) as a subprocess that builds a TypeScript LanguageService ONCE per root and answers all occurrence positions in one batch call. The adapter runs occurrence-driven (same pattern as Python), reusing the core `SpanIndex` and `SymbolResolver` contract already on `main`.

**Tech Stack:** Python 3.13, tree-sitter-typescript, Node.js (external) + TypeScript Compiler API (install-on-demand), pytest, uv workspace, ruff, ty.

## Global Constraints

- Python `>=3.13`; build backend `uv_build`; `src/` layout.
- ruff line-length `79`, target `py313`, full annotations + docstrings on non-test code (tests relaxed); ty must pass on changed src.
- TypeScript adapter coverage `fail_under=100` (Python side; the Node subprocess and `ts_resolver.js` are NOT in Python coverage).
- **No new Python dependencies.** Node is an external tool; the typescript package is install-on-demand into a stdlib-computed cache dir.
- All `Span`/coordinates 1-based (line AND col). tree-sitter 0-based → `+1` (existing `_make_span`). The JS bridge converts 1-based line/col ↔ 0-based TS offset internally — NO coordinate math on the Python side.
- All node IDs via `make_node_id(project_name, qualified_name, kind)`.
- The resolver must NEVER raise — subprocess failure / missing Node / disabled state → queries return `None`/`[]`; the graph still builds (tree-sitter structure + name-level `EXTERNAL_SYMBOL`).
- Reuse from `main` unchanged: `graphlens.utils.SpanIndex`, `graphlens.contracts.{SymbolResolver, ResolvedRef, Occurrence}`, model kinds (`VARIABLE/ATTRIBUTE/TYPE_ALIAS/HAS_TYPE`).
- Pinned TypeScript version constant `_TS_VERSION = "5.8.3"`.
- Conventional-commit messages; commit after every task/cycle.

## Reference implementation

The Python adapter on `main` (`packages/graphlens-python/src/graphlens_python/`) is the proven template for the occurrence model, the unified `_scan_value` traversal, value-references, and the resolution pass. Where this plan says "mirror Python `<thing>`", read that file and replicate the structure, adapting tree-sitter node types to TypeScript grammar. The TS grammar differs: `member_expression` (not `attribute`), `type_identifier`, `interface_declaration`, `enum_declaration`, `type_alias_declaration`, `lexical_declaration`/`variable_declarator`, `call_expression` (not `call`), `arguments` (not `argument_list`), `statement_block` (not `block`).

## Global Interfaces (locked — match these names/types verbatim)

**`TsResolver` — `packages/graphlens-typescript/src/graphlens_typescript/_resolver.py`:**
```python
_TS_VERSION = "5.8.3"

# A query is (absolute file path, 1-based line, 1-based col)
Query = tuple[Path, int, int]

class TsResolver(SymbolResolver):
    def __init__(self, ts_version: str = _TS_VERSION) -> None: ...
    def prepare(self, project_root: Path, files: list[Path]) -> None: ...
    def resolve_all(self, queries: list[Query]) -> list[ResolvedRef | None]: ...
    # contract methods delegate to resolve_all([(file, line, col)]):
    def definition_at(self, file, line, col) -> ResolvedRef | None: ...
    def infer_type_at(self, file, line, col) -> ResolvedRef | None: ...
    def references_to(self, file, line, col) -> list[Occurrence]: ...
    # pure/mediated helpers (mock subprocess.run, not these):
    def _build_request(self, queries) -> dict: ...
    def _parse_response(self, payload: dict) -> list[ResolvedRef | None]: ...
    def _run_bridge(self, request: dict) -> dict: ...   # the only subprocess touch
```
- `prepare` sets `self._root`, runs `ensure_typescript()`; on any failure sets `self._disabled = True`.
- When `_disabled`, `resolve_all` returns `[None] * len(queries)` without touching Node.
- `_build_request(queries)` → `{"project_root": str(self._root), "queries": [{"file": str(f), "line": l, "col": c} for (f,l,c) in queries]}`.
- `_parse_response(payload)` maps `payload["results"]` (list of dicts or null) to `ResolvedRef(full_name=name, file_path=Path(file), line, col, kind, origin)` or `None`.

**JS bridge — `ts_resolver.js` (package data):** stdin `{project_root, queries:[{file,line,col}]}` (1-based) → stdout `{results:[{file,line,col,name,kind,origin}|null]}` (1-based). Builds one LanguageService (`skipLibCheck:true`), `getDefinitionAtPosition` per query, converts offsets, classifies origin. Never throws (per-query try/catch → null).

**`OccurrenceRef` — collected by the TS visitor (mirror Python):**
```python
@dataclass(frozen=True)
class OccurrenceRef:
    role: str           # call|read|write|annotation|base
    line: int           # 1-based
    col: int            # 1-based
    enclosing_id: str
    span: Span
```
- `TypescriptASTVisitor` exposes `self.occurrences: list[OccurrenceRef]` and `self.abs_file_path: str` after `visit()`. Structural nodes carry `metadata["name_span"]` and use an ABSOLUTE `file_path` (`str(ctx.file_path)`), matching Python and the resolver's absolute paths. The FILE node keeps its relative path. The visitor no longer emits `CALLS`/`INHERITS_FROM`.

**Adapter (mirror Python `_adapter.py`):** `_ROLE_TO_KIND = {call:CALLS, base:INHERITS_FROM, annotation:HAS_TYPE, read:REFERENCES, write:REFERENCES}`; `_ensure_external_symbol(graph, project_name, qname, origin)`; batch `_resolve_occurrences(graph, project_name, resolver, span_index, occurrences)` that calls `resolver.resolve_all(...)` ONCE.

---

## Task 1: `TsResolver` + Node bridge (install-on-demand, batch)

**Files:**
- Create: `packages/graphlens-typescript/src/graphlens_typescript/_resolver.py`
- Create: `packages/graphlens-typescript/src/graphlens_typescript/ts_resolver.js` (package data)
- Create: `packages/graphlens-typescript/src/graphlens_typescript/_ts_bridge_package.json` (template, package data)
- Modify: `packages/graphlens-typescript/pyproject.toml` (include the two assets as package data)
- Test: `packages/graphlens-typescript/tests/test_typescript_resolver.py`

**Interfaces:**
- Consumes: `SymbolResolver`, `ResolvedRef`, `Occurrence` (core, on `main`).
- Produces: `TsResolver` (signature in Global Interfaces).

> Two cycles: 1a Python `TsResolver` (mocked subprocess, 100% coverage); 1b the JS bridge (verified by the gated integration test in Task 5). Commit after each.

### 1a — Python `TsResolver`

- [ ] **Step 1: Write the failing tests**

Create `packages/graphlens-typescript/tests/test_typescript_resolver.py`:
```python
from pathlib import Path
from unittest.mock import patch

from graphlens.contracts import ResolvedRef
from graphlens_typescript._resolver import TsResolver, _TS_VERSION


def test_build_request_shape():
    r = TsResolver()
    r._root = Path("/proj")
    req = r._build_request([(Path("/proj/a.ts"), 3, 5)])
    assert req == {
        "project_root": "/proj",
        "queries": [{"file": "/proj/a.ts", "line": 3, "col": 5}],
    }


def test_parse_response_maps_results():
    r = TsResolver()
    payload = {"results": [
        {"file": "/proj/b.ts", "line": 1, "col": 10,
         "name": "helper", "kind": "function", "origin": "internal"},
        None,
    ]}
    out = r._parse_response(payload)
    assert out[0] == ResolvedRef(
        full_name="helper", file_path=Path("/proj/b.ts"),
        line=1, col=10, kind="function", origin="internal")
    assert out[1] is None


def test_disabled_resolver_returns_none(tmp_path):
    r = TsResolver()
    r._disabled = True
    r._root = tmp_path
    assert r.resolve_all([(tmp_path / "a.ts", 1, 1)]) == [None]


def test_resolve_all_runs_bridge_once():
    r = TsResolver()
    r._root = Path("/proj")
    r._disabled = False
    payload = {"results": [
        {"file": "/proj/b.ts", "line": 2, "col": 3,
         "name": "f", "kind": "function", "origin": "internal"}]}
    with patch.object(r, "_run_bridge", return_value=payload) as m:
        out = r.resolve_all([(Path("/proj/a.ts"), 1, 1)])
    m.assert_called_once()
    assert out[0].full_name == "f"


def test_resolve_all_swallows_bridge_error():
    r = TsResolver()
    r._root = Path("/proj")
    r._disabled = False
    with patch.object(r, "_run_bridge", side_effect=RuntimeError("boom")):
        assert r.resolve_all([(Path("/proj/a.ts"), 1, 1)]) == [None]


def test_contract_methods_delegate():
    # cover definition_at / infer_type_at / references_to for 100%
    r = TsResolver()
    r._root = Path("/proj")
    r._disabled = False
    payload = {"results": [
        {"file": "/p/x.ts", "line": 1, "col": 1,
         "name": "f", "kind": "function", "origin": "internal"}]}
    with patch.object(r, "_run_bridge", return_value=payload):
        assert r.definition_at(Path("/proj/a.ts"), 1, 1).full_name == "f"
        assert r.infer_type_at(Path("/proj/a.ts"), 1, 1).full_name == "f"
    assert r.references_to(Path("/proj/a.ts"), 1, 1) == []


def test_run_bridge_invokes_node(tmp_path):
    # cover _run_bridge body by mocking subprocess.run (not the method)
    r = TsResolver()
    r._root = tmp_path
    r._cache_dir = tmp_path
    import json
    completed = type("C", (), {"stdout": json.dumps({"results": []}),
                               "returncode": 0})()
    with patch("graphlens_typescript._resolver.subprocess.run",
               return_value=completed) as m:
        out = r._run_bridge({"project_root": str(tmp_path), "queries": []})
    m.assert_called_once()
    assert out == {"results": []}


def test_ensure_typescript_skips_when_sentinel_present(tmp_path):
    r = TsResolver()
    sentinel = tmp_path / "node_modules" / "typescript" / "lib"
    sentinel.mkdir(parents=True)
    (sentinel / "typescript.js").write_text("")
    r._cache_dir = tmp_path
    with patch("graphlens_typescript._resolver.subprocess.run") as m:
        r.ensure_typescript()
    m.assert_not_called()  # already installed → no npm
```

- [ ] **Step 2: Run to verify they fail** — `ModuleNotFoundError: graphlens_typescript._resolver`.

- [ ] **Step 3: Implement `_resolver.py`**

```python
"""TypeScript type-aware resolver via a Node subprocess (Compiler API)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from graphlens.contracts import Occurrence, ResolvedRef, SymbolResolver

if TYPE_CHECKING:
    pass

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
        self._ts_version = ts_version
        self._root: Path | None = None
        self._cache_dir: Path = _cache_root() / ts_version
        self._disabled = False

    def prepare(self, project_root: Path, files: list[Path]) -> None:
        self._root = project_root
        try:
            self.ensure_typescript()
        except Exception:  # noqa: BLE001 — never raise out of the resolver
            logger.warning("TsResolver disabled: typescript unavailable")
            self._disabled = True

    def ensure_typescript(self) -> None:
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
        subprocess.run(  # noqa: S603
            ["npm", "install",  # noqa: S607
             f"typescript@{self._ts_version}",
             "--no-save", "--no-audit", "--prefer-offline"],
            cwd=str(self._cache_dir),
            check=True, capture_output=True, timeout=300,
        )

    def resolve_all(self, queries: list[Query]) -> list[ResolvedRef | None]:
        if self._disabled or not queries or self._root is None:
            return [None] * len(queries)
        try:
            payload = self._run_bridge(self._build_request(queries))
            return self._parse_response(payload)
        except Exception:  # noqa: BLE001
            logger.warning("TsResolver batch failed; degrading to None")
            return [None] * len(queries)

    def definition_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        return self.resolve_all([(file, line, col)])[0]

    def infer_type_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        return self.resolve_all([(file, line, col)])[0]

    def references_to(
        self, file: Path, line: int, col: int
    ) -> list[Occurrence]:
        return []  # references batch not used by the resolution pass; deferred

    def _build_request(self, queries: list[Query]) -> dict:
        return {
            "project_root": str(self._root),
            "queries": [
                {"file": str(f), "line": ln, "col": c}
                for (f, ln, c) in queries
            ],
        }

    def _parse_response(self, payload: dict) -> list[ResolvedRef | None]:
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
        from importlib.resources import files
        bridge = files("graphlens_typescript") / _BRIDGE_JS
        env = dict(os.environ, TS_CACHE_DIR=str(self._cache_dir))
        completed = subprocess.run(  # noqa: S603
            ["node", str(bridge)],  # noqa: S607
            input=json.dumps(request),
            capture_output=True, text=True, env=env,
            cwd=str(self._root), timeout=600, check=False,
        )
        return json.loads(completed.stdout)
```

- [ ] **Step 4: Create the JS bridge asset stubs**

Create `ts_resolver.js` (full implementation in cycle 1b) and `_ts_bridge_package.json` with `{"private":true,"dependencies":{}}`. For now `ts_resolver.js` can be the complete script from cycle 1b — write it now (next cycle's step) so the file exists.

- [ ] **Step 5: Run tests green** — `uv run pytest packages/graphlens-typescript/tests/test_typescript_resolver.py -v`. Add coverage check: `--cov=graphlens_typescript._resolver --cov-report=term-missing` → 100% (every branch: disabled, error-swallow, sentinel-present, _run_bridge body).

- [ ] **Step 6: Commit** — `feat(typescript): add TsResolver (Node bridge, batch, install-on-demand)`.

### 1b — JS bridge `ts_resolver.js`

- [ ] **Step 1: Write the bridge**

Create `packages/graphlens-typescript/src/graphlens_typescript/ts_resolver.js`:
```javascript
"use strict";
const path = require("path");

const cacheDir = process.env.TS_CACHE_DIR;
let ts;
try {
  ts = require(require.resolve("typescript", { paths: [cacheDir] }));
} catch (e) {
  process.stdout.write(JSON.stringify({ results: [], error: "no-typescript" }));
  process.exit(0);
}

function loadConfig(projectRoot) {
  const configPath = ts.findConfigFile(projectRoot, ts.sys.fileExists, "tsconfig.json");
  if (!configPath) {
    const files = ts.sys.readDirectory(projectRoot, [".ts", ".tsx", ".mts", ".cts"]);
    return {
      options: { target: ts.ScriptTarget.ES2020, allowJs: true, skipLibCheck: true },
      fileNames: files,
    };
  }
  const { config, error } = ts.readConfigFile(configPath, ts.sys.readFile);
  if (error) return { options: { skipLibCheck: true }, fileNames: [] };
  const parsed = ts.parseJsonConfigFileContent(
    config, ts.sys, path.dirname(configPath), undefined, configPath);
  parsed.options.skipLibCheck = true;
  return { options: parsed.options, fileNames: parsed.fileNames };
}

function buildService(projectRoot) {
  const { options, fileNames } = loadConfig(projectRoot);
  const host = {
    getScriptFileNames: () => fileNames,
    getScriptVersion: () => "0",
    getScriptSnapshot: (f) =>
      ts.sys.fileExists(f) ? ts.ScriptSnapshot.fromString(ts.sys.readFile(f) || "") : undefined,
    getCurrentDirectory: () => projectRoot,
    getCompilationSettings: () => options,
    getDefaultLibFileName: (o) => ts.getDefaultLibFilePath(o),
    fileExists: ts.sys.fileExists,
    readFile: ts.sys.readFile,
    readDirectory: ts.sys.readDirectory,
    directoryExists: ts.sys.directoryExists,
    getDirectories: ts.sys.getDirectories,
  };
  return ts.createLanguageService(host, ts.createDocumentRegistry());
}

function classifyOrigin(fileName, projectRoot) {
  if (!fileName) return "unknown";
  const n = fileName.replace(/\\/g, "/");
  if (/\/typescript\/lib\/lib\.[^/]+\.d\.ts$/.test(n)) return "stdlib";
  if (n.includes("/node_modules/")) return "third_party";
  const root = path.resolve(projectRoot).replace(/\\/g, "/");
  if (n.startsWith(root + "/")) return "internal";
  return "unknown";
}

function answer(service, projectRoot, q) {
  try {
    const program = service.getProgram();
    if (!program) return null;
    const sf = program.getSourceFile(q.file);
    if (!sf) return null;
    const offset = ts.getPositionOfLineAndCharacter(sf, q.line - 1, q.col - 1);
    const defs = service.getDefinitionAtPosition(q.file, offset);
    if (!defs || defs.length === 0) return null;
    const d = defs[0];
    const dsf = program.getSourceFile(d.fileName);
    const lc = dsf ? dsf.getLineAndCharacterOfPosition(d.textSpan.start) : { line: 0, character: 0 };
    return {
      file: d.fileName, line: lc.line + 1, col: lc.character + 1,
      name: d.name, kind: d.kind,
      origin: classifyOrigin(d.fileName, projectRoot),
    };
  } catch (e) { return null; }
}

async function main() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  let request;
  try { request = JSON.parse(Buffer.concat(chunks).toString()); }
  catch (e) { process.stdout.write(JSON.stringify({ results: [] })); return; }
  const root = path.resolve(request.project_root);
  let service;
  try { service = buildService(root); }
  catch (e) { process.stdout.write(JSON.stringify({ results: request.queries.map(() => null) })); return; }
  const results = request.queries.map((q) =>
    answer(service, root, { ...q, file: path.resolve(root, q.file) }));
  process.stdout.write(JSON.stringify({ results }));
}
main().catch(() => process.stdout.write(JSON.stringify({ results: [] })));
```

- [ ] **Step 2: Configure package data** — in `packages/graphlens-typescript/pyproject.toml`, ensure `ts_resolver.js` and `_ts_bridge_package.json` ship in the wheel. With uv_build add:
```toml
[tool.uv.build-backend]
data = { } # default includes package dir; ensure non-.py files are kept
```
If uv_build excludes non-`.py` by default, add an explicit include for `*.js` and the json template under the package. Verify with `uv build` that the files appear in the wheel.

- [ ] **Step 3: Commit** — `feat(typescript): add ts_resolver.js Compiler API bridge`.

---

## Task 2: src-layout fix + absolute structural file_path

**Files:**
- Modify: `packages/graphlens-typescript/src/graphlens_typescript/_module_resolver.py:16-30` (`find_source_roots`)
- Modify: `packages/graphlens-typescript/src/graphlens_typescript/_visitor.py` (`_make_node` `file_path`)
- Test: `packages/graphlens-typescript/tests/test_typescript_module_resolver.py`, `test_typescript_visitor.py`

**Interfaces:**
- Produces: structural nodes carry an absolute `file_path` (`str(ctx.file_path)`); files outside `src/` are analyzed.

> Two small fixes that the resolution pass depends on (absolute paths for `SpanIndex.at`, and not skipping tests/).

- [ ] **Step 1: Failing test (src-layout)** — in `test_typescript_module_resolver.py`:
```python
def test_src_layout_includes_project_root_for_outside_files(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "mod.ts").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "mod.test.ts").write_text("")
    files = [tmp_path / "src" / "pkg" / "mod.ts",
             tmp_path / "tests" / "mod.test.ts"]
    roots = find_source_roots(tmp_path, files)
    assert roots[0] == tmp_path / "src"
    assert tmp_path in roots
    assert file_to_qualified_name(files[0], roots[0]) == "pkg.mod"
    assert file_to_qualified_name(files[1], tmp_path) == "tests.mod.test"
```
(Note: `mod.test.ts` → `tests.mod.test` because only the final `.ts` is stripped; that's acceptable for this slice.)

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Fix `find_source_roots`** — change `return [src]` to `return [src, project_root]` (src first). The non-src-layout branch still returns `[project_root]`.

- [ ] **Step 4: Failing test (absolute file_path)** — in `test_typescript_visitor.py` (use the existing `parse_and_visit` conftest helper):
```python
def test_structural_node_uses_absolute_file_path(parse_and_visit):
    graph = parse_and_visit("export function foo() {}\n")
    fn = next(n for n in graph.nodes.values() if n.kind.value == "function")
    # absolute path from the visitor context, not the relative one
    assert fn.file_path is not None
    assert fn.file_path.startswith("/") or ":" in fn.file_path
```

- [ ] **Step 5: Fix `_make_node`** — change `file_path=self._ctx.file_relative_path` to `file_path=str(self._ctx.file_path)`. (FILE nodes are created in `_adapter.py` and keep the relative path — do NOT change those.)

- [ ] **Step 6: Run the TS module-resolver + visitor suites green; commit** — `fix(typescript): include project root in src-layout; absolute structural file_path`.

---

## Task 3: Rework the TS visitor — occurrences, name_span, structural nodes, value-references

**Files:**
- Modify: `packages/graphlens-typescript/src/graphlens_typescript/_visitor.py`
- Test: `packages/graphlens-typescript/tests/test_typescript_visitor.py`, `conftest.py`

**Interfaces:**
- Produces: `OccurrenceRef`; `TypescriptASTVisitor.occurrences`/`abs_file_path`; structural nodes with `name_span`; new `TYPE_ALIAS`/`ENUM(as CLASS)`/`VARIABLE`/`ATTRIBUTE` nodes; no `CALLS`/`INHERITS_FROM` emitted by the visitor.

> **Read `packages/graphlens-python/src/graphlens_python/_visitor.py` first** — it is the proven template. Mirror its `OccurrenceRef`, the single `_scan_value` unified traversal (one `read` per value-use identifier, one `call` per call, `write` on assignment targets, no double-count, keyword-arg value-only, no descent into nested defs), `name_span` recording, base/annotation occurrences, and value-references. Adapt tree-sitter node types per the grammar map below. Four cycles, commit each.

**TS grammar map (vs Python):** `call_expression`→call (callee is `identifier` or `member_expression`, trailing `.property`); `arguments`→call args (Python `argument_list`); `statement_block`→body (Python `block`); `member_expression`→attribute access; `lexical_declaration`/`variable_declarator`→assignments; `type_annotation` (`: T`)→annotation; `class_heritage`/`extends_clause`/`implements_clause`/`extends_type_clause`→heritage bases; `type_alias_declaration`→type alias; `enum_declaration`/`enum_body`/`enum_assignment`→enum + members; `public_field_definition`→class field.

### 3.1 — `name_span` on structural nodes
- [ ] Mirror Python cycle 5.1: add a `name_node` param to `_make_node` that records `metadata["name_span"] = _make_span(name_node)`. Thread the already-located name node from `_handle_class`, `_visit_interface_declaration`, `_handle_function`, `_handle_lexical_declaration`, `_extract_parameters`. Failing test asserts a class's `name_span` is at the type name position. Commit `feat(typescript): record name_span on structural nodes`.

### 3.2 — Unified `_scan_value`: collect call/read occurrences, drop direct CALLS
- [ ] Add `OccurrenceRef` (verbatim from Python), `self.occurrences`/`self.abs_file_path` in `__init__`. Replace `_extract_calls`/`_find_calls_in_node` (which build EXTERNAL_SYMBOL + CALLS) with a single `_scan_value(node, enclosing_id)` mirroring Python: a `call_expression` records a `call` occurrence on the callee name node (trailing property for `member_expression`) then scans its `arguments` for value `read`s; identifiers in value position record `read`; it does NOT descend into nested `function/arrow/class/method` nodes. Add a `parse_and_visit_visitor` conftest helper returning `(graph, visitor)`. Failing test: `a(); ` records one `call` and no EXTERNAL_SYMBOL-by-name; `a(b)` records a `read` on `b`; `x = f(a)` records exactly one `read` on `a` (no double count). Keyword/shorthand: object-literal property shorthand and named args read only the value. Commit `feat(typescript): collect call/read occurrences via unified scan`.

### 3.3 — base + annotation occurrences (drop direct INHERITS_FROM)
- [ ] In `_handle_class`/`_visit_interface_declaration`, replace the `INHERITS_FROM`→EXTERNAL_SYMBOL loop with `base` occurrences recorded on each heritage base name node (handle `extends_clause`, `implements_clause`, `extends_type_clause`, `generic_type` leading name). Keep extracting `bases` text for metadata. For `type_annotation` on params, return types, and variable declarations, record an `annotation` occurrence on the type's leading `type_identifier`/`identifier`. Failing test: `class Sub extends Base {}` records a `base`; `function f(x: T): R {}` records `annotation`s. Commit `feat(typescript): collect base + annotation occurrences`.

### 3.4 — structural nodes: TYPE_ALIAS, ENUM, VARIABLE, ATTRIBUTE + value-refs + write
- [ ] Add handlers: `_visit_type_alias_declaration` → `TYPE_ALIAS` node (name from `type_identifier`), record an `annotation` occurrence on the aliased type's leading identifier. `_visit_enum_declaration` → `CLASS` + `metadata["is_enum"]=True`; each `enum_assignment`/`property_identifier` member → `ATTRIBUTE`. Extend `_handle_lexical_declaration`: a `variable_declarator` whose value is NOT a function → `VARIABLE` (or `ATTRIBUTE` if `_kind_stack[-1]==CLASS`) with `metadata["is_constant"]` for `const`; record a `write` occurrence on the name and `read`s on the initializer via `_scan_value`. `public_field_definition` in a class body → `ATTRIBUTE`. Ensure function bodies and class/program scopes route value expressions and standalone `expression_statement`s through `_scan_value` (mirror Python's module/class-body call recording, guarded against double-count inside functions). Value-references (callbacks, decorator args, call args) fall out of `_scan_value`. Failing tests: `type V = string[]` → TYPE_ALIAS; `enum E { A, B }` → CLASS+is_enum with 2 ATTRIBUTE members; `const C = 1; const x = C;` → VARIABLE + `write`/`read`. Commit `feat(typescript): model type aliases/enums/variables + value occurrences`.

---

## Task 4: Adapter batch resolution pass

**Files:**
- Modify: `packages/graphlens-typescript/src/graphlens_typescript/_adapter.py`
- Test: `packages/graphlens-typescript/tests/test_typescript_adapter.py`

**Interfaces:**
- Consumes: `SpanIndex` (core), `TsResolver` (Task 1), `OccurrenceRef`/`visitor.occurrences`/`visitor.abs_file_path` (Task 3).
- Produces: resolved `CALLS`/`REFERENCES`/`HAS_TYPE`/`INHERITS_FROM` to real nodes (or `EXTERNAL_SYMBOL` fallback), via ONE batch `resolve_all`.

> Mirror Python `_adapter.py` `_ROLE_TO_KIND`, `_ensure_external_symbol`, and the resolution helper, but BATCH: collect all occurrences across the root's files, then one `resolver.resolve_all(queries)`.

- [ ] **Step 1: Failing tests** — in `test_typescript_adapter.py`. These need a working resolver, which needs Node+typescript; gate them with a module-level skip OR inject a fake resolver. Prefer a **fake resolver** so the adapter logic is tested without Node:
```python
from pathlib import Path
from graphlens.contracts import ResolvedRef, SymbolResolver

class FakeResolver(SymbolResolver):
    """Resolves every query to a fixed internal target for testing the pass."""
    def __init__(self, target: ResolvedRef | None):
        self._t = target
    def prepare(self, project_root, files): ...
    def resolve_all(self, queries):
        return [self._t for _ in queries]
    def definition_at(self, f, l, c): return self._t
    def infer_type_at(self, f, l, c): return self._t
    def references_to(self, f, l, c): return []


def _edges(graph, kind):
    return [r for r in graph.relations if r.kind.value == kind]


def test_calls_resolve_to_real_function_node(tmp_path):
    (tmp_path / "util.ts").write_text("export function helper() { return 1; }\n")
    (tmp_path / "main.ts").write_text(
        "import { helper } from './util';\nexport function run() { helper(); }\n")
    from graphlens_typescript import TypescriptAdapter
    # point the fake at helper's name position in util.ts (line 1, col 17)
    target = ResolvedRef(full_name="helper",
                         file_path=tmp_path / "util.ts", line=1, col=17,
                         kind="function", origin="internal")
    graph = TypescriptAdapter(resolver=FakeResolver(target)).analyze(tmp_path)
    helper = next(n for n in graph.nodes.values()
                  if n.kind.value == "function" and n.name == "helper")
    assert any(r.target_id == helper.id for r in _edges(graph, "calls"))
    assert all(n.kind.value != "symbol" for n in graph.nodes.values())


def test_unresolved_call_falls_back_to_external(tmp_path):
    (tmp_path / "main.ts").write_text(
        "export function run() { unknownFn(); }\n")
    from graphlens_typescript import TypescriptAdapter
    graph = TypescriptAdapter(resolver=FakeResolver(None)).analyze(tmp_path)
    # ref is None → occurrence skipped; no crash, graph builds
    assert any(n.kind.value == "function" for n in graph.nodes.values())
```
(Compute the exact `col` for `helper`'s name in `util.ts` from the source — `export function ` is 16 chars, so col 17 — and assert it resolves through `SpanIndex.at`.)

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Rework `_adapter.py`**

Add runtime imports: `from pathlib import Path`, `from graphlens.utils import SpanIndex`, `from graphlens_typescript._resolver import TsResolver`, `from graphlens_typescript._visitor import OccurrenceRef`. Import `SymbolResolver` under `TYPE_CHECKING`. Give `TypescriptAdapter.__init__` an injectable `resolver: SymbolResolver | None = None` (default `TsResolver()`), thread it into both `_analyze_root` calls. Add `_ROLE_TO_KIND`, `_ensure_external_symbol`, and a batch resolution helper (mirror Python, but batched):
```python
_ROLE_TO_KIND = {
    "call": RelationKind.CALLS,
    "base": RelationKind.INHERITS_FROM,
    "annotation": RelationKind.HAS_TYPE,
    "read": RelationKind.REFERENCES,
    "write": RelationKind.REFERENCES,
}


def _ensure_external_symbol(graph, project_name, qname, origin):
    sym_id = make_node_id(project_name, qname, NodeKind.EXTERNAL_SYMBOL.value)
    if sym_id not in graph.nodes:
        graph.add_node(Node(
            id=sym_id, kind=NodeKind.EXTERNAL_SYMBOL,
            qualified_name=qname, name=qname.rsplit(".", maxsplit=1)[-1],
            metadata={"origin": origin}))
    return sym_id


def _resolve_occurrences(graph, project_name, resolver, span_index,
                          occurrences):
    # occurrences: list[tuple[str abs_path, OccurrenceRef]]
    queries = [(Path(p), o.line, o.col) for (p, o) in occurrences]
    refs = resolver.resolve_all(queries)
    for (_p, occ), ref in zip(occurrences, refs, strict=True):
        if ref is None:
            continue
        target_id = None
        if ref.origin == "internal" and ref.file_path is not None:
            target_id = span_index.at(str(ref.file_path), ref.line, ref.col)
        if target_id is None:
            target_id = _ensure_external_symbol(
                graph, project_name, ref.full_name or occ.role, ref.origin)
        metadata: dict[str, object] = {"span": occ.span}
        if occ.role in ("read", "write"):
            metadata["access"] = occ.role
        graph.add_relation(Relation(
            source_id=occ.enclosing_id, target_id=target_id,
            kind=_ROLE_TO_KIND[occ.role], metadata=metadata))
```
In `_analyze_root`, accumulate `all_occurrences.extend((visitor.abs_file_path, o) for o in visitor.occurrences)` in the file loop; after the loop build `span_index = SpanIndex.from_graph(graph)`, `resolver.prepare(lang_root, files)`, then `_resolve_occurrences(graph, project_name, resolver, span_index, all_occurrences)` BEFORE the PROJECT→CONTAINS wiring. Thread `resolver` into `_analyze_root`'s signature.

- [ ] **Step 4: Run tests green; fix any pre-existing adapter tests** that asserted the old EXTERNAL_SYMBOL-by-name CALLS shape (update to the new resolved/fallback model — don't gut them).

- [ ] **Step 5: Full TS adapter suite + coverage** — `task typescript:test`. Reach **100%** (cover read/write `access`, the external fallback, the `ref is None` skip — the `FakeResolver` with `None` and with a target gives both paths).

- [ ] **Step 6: Commit** — `feat(typescript): occurrence-driven batch resolution pass`.

---

## Task 5: Migration — package data, integration test, docs, gate

**Files:**
- Create: `packages/graphlens-typescript/tests/test_typescript_bridge_integration.py`
- Modify: `CHANGELOG.md`, `CLAUDE.md`
- Verify: `packages/graphlens-typescript/pyproject.toml` package data

**Interfaces:** none new — verification + docs.

- [ ] **Step 1: Gated integration test** — real Node + typescript end-to-end:
```python
import shutil
import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available")


def test_bridge_resolves_internal_import(tmp_path):
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"strict":false}}')
    (tmp_path / "util.ts").write_text("export function helper() { return 1; }\n")
    (tmp_path / "main.ts").write_text(
        "import { helper } from './util';\nexport function run() { helper(); }\n")
    from graphlens_typescript._resolver import TsResolver
    r = TsResolver()
    r.prepare(tmp_path, list(tmp_path.glob("*.ts")))
    if r._disabled:
        pytest.skip("typescript install unavailable")
    # 'helper' callee in main.ts line 2: 'export function run() { helper'
    refs = r.resolve_all([(tmp_path / "main.ts", 2, 24)])
    assert refs[0] is not None
    assert refs[0].full_name == "helper"
    assert str(refs[0].file_path).endswith("util.ts")
    assert refs[0].origin == "internal"
```
(Verify the real column of `helper` in `main.ts` and assert the true value. This test installs typescript on first run — allow time; it's `skipif`-gated and not in coverage.)

- [ ] **Step 2: Verify package data ships** — `cd packages/graphlens-typescript && uv build` then inspect the wheel (`unzip -l dist/*.whl | grep -E 'ts_resolver.js|_ts_bridge'`). Both assets must be present. Fix `pyproject.toml` includes if missing.

- [ ] **Step 3: CHANGELOG** — add under `## [Unreleased]`:
```markdown
### Changed
- TypeScript adapter now resolves CALLS/REFERENCES/HAS_TYPE/INHERITS_FROM to
  real declaration nodes via the TypeScript Compiler API (Node subprocess,
  install-on-demand). Alias imports (tsconfig paths) resolve. src-layout files
  outside src/ are analyzed. Resolution requires Node; degrades to tree-sitter
  structure when unavailable.
```

- [ ] **Step 4: CLAUDE.md** — in §4, add one line: the TS adapter's `SymbolResolver` is a Node-subprocess Compiler-API resolver (batch `resolve_all`), in contrast to Python's in-process jedi. No other principle changes (model/SpanIndex/occurrence pattern already documented).

- [ ] **Step 5: Export `TsResolver`** — add to `packages/graphlens-typescript/src/graphlens_typescript/__init__.py` (`__all__`).

- [ ] **Step 6: Full gate** — `task core:lint && task core:test && task typescript:lint && task typescript:test`. Confirm: ruff+ty clean, TS adapter 100% coverage (Python side), core unchanged. The gated integration test is skipped/passes depending on Node.

- [ ] **Step 7: Commit** — `docs(typescript): changelog + CLAUDE note; ship bridge assets; export TsResolver`.

---

## Notes for the implementer
- **The Python adapter on `main` is your reference** for the occurrence model, `_scan_value` unification (incl. no-double-count and keyword-value-only), value-references, and the resolution pass. Replicate its structure; only the tree-sitter node types and the resolver (Node batch vs jedi) differ.
- **Coordinate convention**: 1-based line/col everywhere on the Python side. The JS bridge owns the offset conversion. Don't add coordinate math in Python.
- **Absolute paths** key `SpanIndex` and flow through occurrences/queries; the TS Compiler API returns absolute `fileName`. Structural nodes must store absolute `file_path` (Task 2).
- **Never let the resolver raise**; a `None` ref just skips the occurrence (edge not emitted), exactly like Python.
- **Batch, not per-call**: the adapter calls `resolve_all` ONCE per root with all occurrence queries — never `definition_at` in a loop (that would rebuild the LanguageService each time).
