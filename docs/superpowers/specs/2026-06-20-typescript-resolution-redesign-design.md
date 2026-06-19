# TypeScript Resolution Redesign — Design Spec

- **Date:** 2026-06-20
- **Status:** Approved (Section 1 confirmed; Sections 2–3 authored under "make it work" mandate)
- **Scope:** Bring the TypeScript adapter to full parity with the Python adapter's resolved graph — calls/references/types resolved to real declaration nodes via the TypeScript Compiler API. Reuses the language-agnostic core already on `main` (model, `SpanIndex`, `SymbolResolver` contract). IMPLEMENTS/namespace/generics/JSX/incrementality are out of scope (deferred).

---

## 1. Context & Problem

The Python adapter was reworked (merged to `main`) so calls/references/types resolve to real declaration nodes (`SpanIndex`, `SymbolResolver` contract, `JediResolver`, occurrence-driven resolution pass, value-references). The TypeScript adapter is still the pre-redesign shape: the visitor emits `CALLS`/`INHERITS_FROM` directly to name-level `EXTERNAL_SYMBOL` nodes, has no occurrence model / `name_span`, does not model `enum`/`type alias`/`variable`, ignores `tsconfig.json` path mappings, and has the same src-layout skip bug. This spec brings TypeScript to parity.

**Goal:** the TS graph statically holds PyCharm-level connectivity — `CALLS`/`REFERENCES`/`HAS_TYPE`/`INHERITS_FROM` bound to real nodes, value-uses (callbacks/`Depends`-style) as `REFERENCES`, whole project (incl. tests) analyzed, alias imports resolved.

## 2. Architectural Decisions (chosen with the user)

| Axis | Decision |
|---|---|
| Engine | **TypeScript Compiler API** (LanguageService) — full type inference, follows re-exports + inheritance, reads `tsconfig.json` (so `paths`/`baseUrl` alias imports resolve) |
| Bridge | **Bundled Node subprocess** running a JS script (`ts_resolver.js`); Python ↔ Node over JSON stdin/stdout |
| Invocation | **Batch** — build the LanguageService ONCE per root, answer all occurrence positions in one Node call (Program build is expensive; per-call would rebuild) |
| typescript dependency | **Install-on-demand** — `npm install typescript@<pinned>` into a user-cache dir on first use, sentinel-checked + reused (no network on later runs); graceful degradation if Node/npm absent |
| Coordinates | Contract stays **1-based line/col**; the JS bridge converts line/col ↔ character-offset (TS Compiler API is offset-based) — isolated in the bridge, mirroring `JediResolver`'s ±1 |
| Scope | **Full parity with Python** + src-layout fix |

## 3. Reuse from `main` (no changes to these)

- Model: `NodeKind` (incl. `VARIABLE/ATTRIBUTE/TYPE_ALIAS`), `RelationKind` (incl. `HAS_TYPE`), `Node`/`Relation`/`GraphLens`.
- `graphlens.utils.SpanIndex` (location→node bridge by `name_span`).
- `graphlens.contracts.SymbolResolver` ABC + `ResolvedRef`/`Occurrence` DTOs.
- `make_node_id`, `Span`, `collect_marker_roots`, `filter_nested_root_files`.

## 4. Graph Model (TypeScript)

Same node/relation kinds as Python. TS-specific modeling collapses:
- `interface X {}` → `CLASS` + `metadata["is_interface"]=True` (already done in current TS visitor; keep).
- `enum E {}` → `CLASS` + `metadata["is_enum"]=True`; members → `ATTRIBUTE`.
- `type X = ...` → `TYPE_ALIAS`.
- `const`/`let`/`var` non-function bindings → `VARIABLE` (`metadata["is_constant"]` for `const`); function-valued → `FUNCTION`/`METHOD` (existing arrow-function handling).
- class fields/properties → `ATTRIBUTE`.
- Edges resolved by the adapter pass (visitor no longer emits `CALLS`/`INHERITS_FROM`): `CALLS` (caller→real callee), `REFERENCES` (read/write + value-uses, `metadata["access"]`), `HAS_TYPE` (var/param/attr→type), `INHERITS_FROM` (extends/implements→real CLASS or EXTERNAL_SYMBOL), `RESOLVES_TO` (imports, kept). Every node carries `metadata["language"]="typescript"`.

## 5. `TsResolver` + Node Bridge

### 5.1 Python side — `_resolver.py`

`TsResolver(SymbolResolver)`. Split into pure logic (unit-testable without Node) and a thin subprocess shell (mockable):
- `prepare(project_root, files)`: record root; `ensure_typescript()` (install-on-demand, §5.3). If Node/npm/typescript unavailable → set a disabled flag (all queries return `None`/`[]`; graph degrades to tree-sitter structure + name-level `EXTERNAL_SYMBOL`).
- `resolve_all(queries: list[Query]) -> list[ResolvedRef | None]` — the batch entry point the adapter uses. Builds one JSON request, runs the Node bridge once, parses the response, maps each result to a `ResolvedRef` (1-based line/col, origin) or `None`. `Query = {file: Path, line: int, col: int}` (1-based).
- `definition_at`/`infer_type_at`/`references_to` (contract): delegate to `resolve_all([q])` for single queries / compatibility.
- Pure helpers (100% unit-tested with a mocked runner): `_build_request(root, queries) -> dict`, `_parse_response(dict) -> list[ResolvedRef|None]`, `_run_bridge(request) -> dict` (the only subprocess touch — mocked in tests).

DTO note: `ResolvedRef.origin ∈ {stdlib, internal, third_party, unknown}` exactly as Python.

### 5.2 JS bridge — `ts_resolver.js` (asset bundled in the package)

- Reads `{project_root, queries:[{file,line,col}]}` from stdin.
- `loadConfig(root)`: `ts.findConfigFile` → `ts.readConfigFile` → `ts.parseJsonConfigFileContent(config, ts.sys, dirname(configPath), undefined, configPath)` (absolute basePath). No tsconfig → synthesized `compilerOptions` (`target ES2020`, `allowJs`, `skipLibCheck:true`) + `ts.sys.readDirectory` for files.
- Builds ONE `LanguageService` via a `LanguageServiceHost` over the config's `fileNames`, `skipLibCheck:true`, `getScriptVersion: () => "0"`.
- Per query: convert (line-1, col-1) → offset via `ts.getPositionOfLineAndCharacter(sourceFile, line-1, col-1)`; `service.getDefinitionAtPosition(file, offset)`; take first `DefinitionInfo`; convert `textSpan.start` → 1-based line/col via `getLineAndCharacterOfPosition` (+1); classify origin from `fileName`.
- Writes `{results:[{file,line,col,name,kind,origin}|null]}`. Everything in `try/catch`; a failed query → `null`, never crashes the batch.
- Origin classification (in JS): `/typescript\/lib\/lib\..*\.d\.ts$/` → stdlib; path contains `node_modules` → third_party; under project root → internal; else unknown.

### 5.3 Install-on-demand

- Cache dir: `$XDG_CACHE_HOME/graphlens/ts-resolver/<pinned-version>/` (fallback `~/.cache/...`), stdlib only — no new Python dependency.
- Sentinel: `node_modules/typescript/lib/typescript.js`. If present → skip install. Else write a `{"private":true}` `package.json` and run `npm install typescript@<pinned> --no-save --no-audit --prefer-offline` in the cache dir.
- The Node bridge `require`s typescript from the cache dir (`require.resolve("typescript", {paths:[cacheDir]})`), passed via env.
- Pinned version constant in `_resolver.py` (e.g. `_TS_VERSION = "5.8.3"`).

### 5.4 Coordinates (the #1 bug source)

graphlens contract: 1-based line AND col. tree-sitter occurrences: already 1-based (`_make_span` +1). The JS bridge does line/col(1-based) → (line-1,col-1) → offset on input, and offset → (line,char)(0-based) → +1 on output. No coordinate math on the Python side beyond passing 1-based through.

## 6. TypeScript Visitor Rework

Mirror the Python `_scan_value` unification:
- Single value-recorder traversal: each value-use identifier → exactly one `read`; each call → one `call`; assignment targets → `write`; no double-count. Do NOT descend into nested function/class definitions (visited separately).
- Record `metadata["name_span"]` on every structural node (CLASS/INTERFACE/FUNCTION/METHOD/PARAMETER/VARIABLE/ATTRIBUTE/TYPE_ALIAS/ENUM).
- Collect `OccurrenceRef{role, line, col, enclosing_id, span}`, roles `call|read|write|annotation|base`. Visitor no longer emits `CALLS`/`INHERITS_FROM`.
- New structural handlers: `type_alias_declaration` → `TYPE_ALIAS`; `enum_declaration` → `CLASS`+`is_enum` with members as `ATTRIBUTE`; variable declarations → `VARIABLE` (non-function) keeping the existing arrow-function→`FUNCTION/METHOD` path.
- `extends_clause`/`implements_clause` heritage → `base` occurrences (on the base type name). Type annotations (`: T`, generics' leading identifier) → `annotation` occurrences → `HAS_TYPE`. Value-uses (call args, decorator args, callbacks) → `read` (FastAPI-style parity; keyword/property-shorthand handled so only values are read).
- TS grammar uses two parsers (ts vs tsx) — keep the existing `parse_typescript(bytes, tsx=...)` split.

## 7. Adapter Batch Resolution Pass + src-layout fix

`_analyze_root` reworked:
1. tree-sitter pass over all files: structural nodes (with `name_span`) + accumulate `(abs_file_path, OccurrenceRef)`.
2. `SpanIndex.from_graph(graph)`.
3. `resolver.prepare(lang_root, files)` then ONE `resolver.resolve_all(all_queries)` where each query is `(abs_file_path, occ.line, occ.col)`.
4. For each occurrence + its resolved ref: `role→RelationKind` (`call→CALLS, base→INHERITS_FROM, annotation→HAS_TYPE, read/write→REFERENCES`); internal target → `SpanIndex.at(str(ref.file_path), ref.line, ref.col)`; else `EXTERNAL_SYMBOL` with origin; emit edge `source=enclosing_id`, `metadata` (span; access for read/write). `ref is None` → skip.
5. **src-layout fix**: `find_source_roots` returns `[src, project_root]` (not `[src]`) so files outside `src/` (tests, scripts) are analyzed — same fix applied to Python.

`TypescriptAdapter.__init__` gains injectable `resolver: SymbolResolver | None = None` (default `TsResolver()`), threaded into `_analyze_root`, mirroring `dep_parsers`.

## 8. Performance & Robustness

- `skipLibCheck:true` (major speedup), build LanguageService once per root, one Node call per root for the whole batch.
- Node bridge never crashes: per-query `try/catch` → `null`; missing tsconfig → synthesized options; file not in program → `null`.
- `TsResolver` never raises: subprocess errors / non-zero exit / disabled state → all queries `None`. Graph still builds (tree-sitter structure + name-level externals).
- Build cost is real (seconds on large repos); acceptable for v1. Incremental/persistent-LS is deferred.

## 9. Testing

- `tests/test_typescript_resolver.py`: `TsResolver` logic (request build, response parse, coordinate pass-through, origin mapping, disabled-state degradation) at **100%** by mocking at the `subprocess` boundary (patch `subprocess.run`/`Popen`, NOT the `_run_bridge` method itself — so `_run_bridge`'s body, command assembly, and stdout parsing are covered). Same for `ensure_typescript` (patch `subprocess.run` for the npm install + the sentinel path). No Node needed.
- `tests/test_typescript_bridge_integration.py`: real Node + typescript end-to-end on a tmp_path TS project — `@pytest.mark.skipif` when `node`/install unavailable; verifies definition resolution + alias (tsconfig `paths`) resolution. Not counted toward coverage (subprocess).
- Visitor/adapter tests mirror Python: occurrences with roles + `name_span`; resolved `CALLS` to real FUNCTION/METHOD; cross-file; `HAS_TYPE`; `INHERITS_FROM` to internal CLASS; `enum`/`type alias`/`variable` nodes; value-references; no SYMBOL nodes; src-layout files analyzed.
- Gate: `task typescript:test` coverage `fail_under=100` (Python side), `task typescript:lint` (ruff+ty), `task core:*` unchanged.

## 10. Dependencies & Migration

- **No new Python dependencies** (Node is an external tool; cache via stdlib). The JS bridge + a tiny `package.json` template are package data assets.
- `ts_resolver.js` + a `package.json` template shipped as package data: included in the wheel via uv_build package-data config, read at runtime via `importlib.resources.files("graphlens_typescript")`.
- CHANGELOG `[Unreleased]`: TS adapter now resolves to real nodes via TS Compiler API; alias imports resolved; src-layout fix; requires Node + (auto-installed) typescript for resolution.
- CLAUDE.md §4/§5/§9 already language-agnostic post-Python; add a note that TS uses a Node-subprocess resolver (batch) vs Python's in-process jedi.

## 11. Deferred (explicit non-goals)

- `IMPLEMENTS` edge + interface-impl modeling; namespace→`MODULE`; generics-aware edges; JSX/React component usage as calls.
- Persistent LanguageService / incremental per-commit resolution (the slow-build concern); dedicated `READS`/`WRITES` edges.
- Bundling typescript (chose install-on-demand); tsserver protocol (chose Compiler API).

## 12. Sources

- TypeScript Compiler API research (TS 5.x): LanguageService vs Program, `getDefinitionAtPosition`/`DefinitionInfo`, offset↔line/col via `getPositionOfLineAndCharacter`/`getLineAndCharacterOfPosition`, `parseJsonConfigFileContent` (paths/baseUrl), origin classification by fileName, install-on-demand pattern, batch protocol, `skipLibCheck` perf. Sources: TS wiki "Using the Compiler API" / "Language Service API", TS `src/services/types.ts`.
- Reuses the Python redesign spec (`2026-06-18-graph-resolution-redesign-design.md`) for the model, SpanIndex bridge, occurrence-driven pattern, and value-references design.
