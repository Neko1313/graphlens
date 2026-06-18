# Graph Resolution Redesign — Design Spec

- **Date:** 2026-06-18
- **Status:** Approved (Section 1 confirmed; Sections 2–3 authored under "do everything to reach the goals" mandate)
- **Scope of this spec:** First vertical slice — new graph model + resolver contract + Python adapter (jedi). TypeScript, deferred edges, incrementality, and cross-language links are explicitly out of scope (separate spec→plan→implementation cycles).

---

## 1. Context & Problem

graphlens is a **library** that parses source into a knowledge graph (`GraphLens`: `Node`s + `Relation`s). It is a pure data producer — backends (neo4j or any other) are embedded by the caller, not graphlens's concern.

Today the call graph is **shallow / name-level**: a call `foo()` emits a `SYMBOL` node keyed on the bare name `"foo"` and an edge `caller --CALLS--> SYMBOL("foo")`. The `SYMBOL` is not bound to the `FUNCTION`/`METHOD` node that actually declares `foo`. Consequences:

- "Find all calls of function X" cannot distinguish two different `foo` in different modules, nor a method `a.foo()` from a free function `foo()`.
- No reference (non-call usage) resolution, no type information, no cross-file binding to real declaration nodes.

**Goal:** statically hold the connectivity PyCharm holds — calls and usages resolved to their real declaration nodes — while keeping the per-language separation. Primary product use case: precise **find-usages + call graph** to give agents accurate code-context formulations.

## 2. Architectural Decisions (chosen with the user)

| Axis | Decision |
|---|---|
| Graph goal | All four axes: resolve calls/refs to declarations · structural completeness · single cross-language schema · cross-file resolution via imports |
| Resolution depth | **Full PyCharm-level** semantic accuracy |
| Engine | **Full delegation** to type-aware engines (not a hand-rolled tree-sitter resolver) |
| Integration | **Native per-language APIs** — Python → `jedi` (direct import), TS → TypeScript Compiler API (later) |
| First slice | **Model + resolver contract + Python(jedi)** vertical slice end-to-end |

**Role of tree-sitter changes (CLAUDE.md §4 rewrite):** tree-sitter stays for fast structure extraction and occurrence *roles* (call / read / write / annotation) + spans; the type-aware engine (jedi) provides *resolution* (which declaration a name binds to). They are a tandem, not a replacement: tree-sitter says *what & where*, jedi says *where it resolves to*.

**Cross-language uniformity** lives at the **output schema** level: the core graph model is language-agnostic; each adapter maps its native engine's output into that one schema. Every node carries `metadata["language"]`.

## 3. Graph Model

Three rules every researched system (SCIP, LSIF, stack-graphs, Glean, LSP) agrees on, adopted here:

1. **Occurrence-edge ≠ resolution.** "Mentioned here" (`CALLS`/`REFERENCES` with span) is kept distinct from "resolved to there".
2. **Store the answer, not the solver.** stack-graphs runs a resolution machine at query time; graphlens resolves *eagerly* during analysis, so the graph stores the finished `CALLS caller→callee` edge, not scope/push/pop machinery.
3. **`EXTERNAL_SYMBOL` fallback with `origin`** so a resolution edge is never missing when the target is out of graph; and every reference/call carries its **enclosing node** (the edge's `source` is the function the usage lives in) so find-usages can answer "in which function".

### 3.1 Node kinds

Existing: `PROJECT, MODULE, FILE, CLASS, METHOD, FUNCTION, PARAMETER, IMPORT, EXTERNAL_SYMBOL`.

Added for Python v1:

| Node kind | Purpose | Tier |
|---|---|---|
| `VARIABLE` | module/local value bindings — read/write & `HAS_TYPE` targets | HIGH-VALUE |
| `ATTRIBUTE` | class members (field/property) — member-access `a.b` targets | HIGH-VALUE |
| `TYPE_ALIAS` | `X: TypeAlias = ...` — resolution must chase through it | HIGH-VALUE |

Modeling collapses (avoid entity proliferation):
- `ENUM` → `CLASS` + `metadata["is_enum"]=True`; members → `ATTRIBUTE` (a Python enum *is* a class).
- `CONSTANT` → `VARIABLE` + `metadata["is_constant"]=True`.
- **`SYMBOL` is removed** — its callee-by-name role is replaced by resolution to a real node or `EXTERNAL_SYMBOL`.

`DEPENDENCY` stays unused in this slice.

### 3.2 Relation kinds

Existing: `CONTAINS, DECLARES, IMPORTS, RESOLVES_TO, INHERITS_FROM` (kept). `CALLS`/`REFERENCES` reworked; `HAS_TYPE` added. `DEPENDS_ON` unused.

| Relation | Before → After | Tier |
|---|---|---|
| `CALLS` | `FUNCTION/METHOD → SYMBOL(name)` → **`caller → real callee node`** (resolved via jedi `goto`/`infer`); call-site spans in `metadata["call_sites"]`, count in `metadata["count"]` | ESSENTIAL |
| `REFERENCES` | *new* — non-call, non-annotation usage (reading a variable's value, using a class/function as a value — decorator, callback); `source` = enclosing node, `metadata["access"]` ∈ `read`/`write`, occurrence span in `metadata["span"]`. Type annotations produce `HAS_TYPE`, not `REFERENCES`. | ESSENTIAL |
| `RESOLVES_TO` | kept, now resolves to internal `CLASS/FUNCTION/...` too, not only `MODULE` | ESSENTIAL |
| `INHERITS_FROM` | now binds to the real internal `CLASS` when resolvable (jedi MRO), `EXTERNAL_SYMBOL` otherwise | ESSENTIAL |
| `HAS_TYPE` | *new* — `VARIABLE/PARAMETER/ATTRIBUTE → CLASS` (type from annotation/inference) | HIGH-VALUE |

`READS`/`WRITES` are encoded as `metadata["access"]` on `REFERENCES` in v1 (SCIP encodes them as bit flags; they can be promoted to dedicated relation kinds later without a schema break).

### 3.3 Resulting queries

- "Who calls X" = incoming `CALLS` edges with `target == X.id`.
- "All usages of X" = incoming `CALLS` + `REFERENCES` with `target == X.id`.
- "In which function" = the edge's `source` (the enclosing node).
- Inherited-method calls are correct because `INHERITS_FROM` + jedi MRO resolve the attribute to the base-class method.

## 4. Resolver Contract & Location→Node Bridge

### 4.1 `SpanIndex` (core, `src/graphlens/utils/span_index.py`)

Built from the graph's structural nodes; maps a source position to a node:
- `enclosing(file, line, col) -> node_id | None` — innermost definition whose span contains the position (the caller / usage owner).
- `at(file, line, col) -> node_id | None` — the definition whose **name span** is at the position (the resolved target, when internal).

Because jedi returns a definition's location as the *name* position (not the `def`/`class` keyword), every structural node carries `metadata["name_span"]: Span` (the identifier's span). `SpanIndex` indexes `enclosing` by the node's full `span` and `at` by its `name_span`.

This is the reusable bridge (LSP/Glean lesson: map a bare `Location` back to its enclosing/declaring node).

### 4.2 `SymbolResolver` (core, `src/graphlens/contracts/resolver.py`)

Lightweight ABC marking the type-aware engine seam, mirroring jedi/LSP primitives:

```python
class SymbolResolver(ABC):
    @abstractmethod
    def prepare(self, project_root: Path, files: list[Path]) -> None: ...
    @abstractmethod
    def definition_at(self, file: Path, line: int, col: int) -> ResolvedRef | None: ...
    @abstractmethod
    def infer_type_at(self, file: Path, line: int, col: int) -> ResolvedRef | None: ...
    @abstractmethod
    def references_to(self, file: Path, line: int, col: int) -> list[Occurrence]: ...
```

Language-agnostic DTOs (core):
- `ResolvedRef{ full_name: str, file_path: Path | None, line: int, col: int, kind: str, origin: str }`
- `Occurrence{ file_path: Path, line: int, col: int, is_definition: bool, access: str }`

`origin ∈ {stdlib, internal, third_party, unknown}`. The ABC is justified by being proven against jedi now and reused by the TS resolver later; if it proves premature it can be dropped — but the `SpanIndex` bridge stands regardless.

### 4.3 Coordinate convention

jedi returns **1-based line / 0-based column**; graphlens `Span` is **1-based on both**. The bridge adds `+1` to the column in exactly one place. (Both research reports flag this as the dominant off-by-one source.)

## 5. Python Adapter Architecture

New module `packages/graphlens-python/src/graphlens_python/_resolver.py` (`JediResolver` wrapping a single shared `jedi.Project`). `_visitor.py` extended; `_adapter.py:_analyze_root` reworked.

### 5.1 `_analyze_root` flow (occurrence-driven)

1. **tree-sitter pass** (`_visitor.py`): build structural nodes (`CLASS/FUNCTION/METHOD/PARAMETER/VARIABLE/ATTRIBUTE/TYPE_ALIAS/IMPORT`), each storing `metadata["name_span"]` (identifier position) for the location→node bridge, and collect a list of **occurrences** `{file, position, role, enclosing_node_id}` where `role ∈ {call, read, write, annotation, base}`. The enclosing node is known from the visitor's scope stack. No resolution here.
2. Build `SpanIndex` from the structural nodes.
3. **jedi pass** (`_resolver.py`): for each occurrence, resolve the target:
   - `call` → `definition_at` (with `infer_type_at` for `obj.method()` receivers); emit `CALLS enclosing → target`.
   - `read`/`write` → `definition_at`; emit `REFERENCES enclosing → target` with `metadata["access"]`.
   - `annotation` / parameter type → `infer_type_at`; emit `HAS_TYPE`.
   - `base` (class heritage) → `definition_at`; emit `INHERITS_FROM`.
   - Internal target → map location to `node_id` via `SpanIndex.at`; else create `EXTERNAL_SYMBOL` with `origin` and emit `RESOLVES_TO` fallback.

**Why occurrence-driven, not definition-driven:** resolve at each call-site via local `goto`/`infer` (cheap) instead of running project-wide `get_references` per definition (expensive, O(modules)). Enclosing comes free from the tree-sitter scope.

### 5.2 Origin classification (from jedi research)

Resolve to a definition, then classify by `module_path`:
1. `module_path is None` or `in_builtin_module()` → treat as **stdlib** (builtins/compiled).
2. `module_path` under `Project.path` → **internal**.
3. `module_path` under the env stdlib path / matches `sys.stdlib_module_names` → **stdlib**.
4. `module_path` contains `site-packages`/`dist-packages` → **third_party**.
5. else → **unknown**.

Caveat: jedi often resolves stdlib/third-party to typeshed `.pyi` stubs inside its own bundle, so classify by `full_name` top-level package + `in_builtin_module()` + an explicit stdlib name-set, not by path matching the live interpreter alone.

## 6. Performance & Robustness

- One shared `jedi.Project` across all files; always pass explicit `path=`. Prefer `goto(follow_imports=True)` over `infer` when only the lexical definition is needed (`infer` is slower).
- jedi/parso is error-recovering: syntax errors do not raise (`get_syntax_errors()` for diagnostics); unresolved imports return `[]` (handle empty, never crash) — matches graphlens's error-tolerant requirement.
- Relations remain append-only and not deduped (existing behavior); consumers dedup. Node creation stays idempotent via deterministic `make_node_id` + presence checks.

## 7. Testing (`fail_under=100`, mirror existing structure)

- `tests/test_resolver.py` — `JediResolver` resolution on a sample project.
- extend `test_visitor.py` — occurrences with roles + new structural node kinds.
- extend `test_adapter.py` — full graph assertions: `CALLS` to real nodes; cross-file imported call; `obj.method()` via type inference; inherited method via MRO; variable read/write `REFERENCES` with `access`; type annotation `HAS_TYPE`; unresolved external symbol with `origin`; `SYMBOL` no longer emitted.
- core: `tests/test_utils_span_index.py`, `tests/test_contracts_resolver.py`.

## 8. Dependencies & Migration

- Add `jedi>=0.19.2` to `packages/graphlens-python/pyproject.toml`.
- **Breaking model change** (remove `SYMBOL`, add node/relation kinds). Project is 0.x → major-ish change acceptable; note in CHANGELOG.
- Rewrite CLAUDE.md **§4** (tree-sitter + type-aware resolver tandem), **§5** (visitor now emits occurrences), **§9** (origin via resolver, not only manifest pre-pass).

## 9. Deferred (explicit non-goals, with reasons)

- **TS resolver** (TypeScript Compiler API) — next spec, reuses `SymbolResolver` + `SpanIndex`.
- **Dedicated `READS`/`WRITES`/`IMPLEMENTS`/`EXPORTS` edges** — SCIP shows these promote from metadata without a schema break; add when needed.
- **`ENUM_MEMBER` / `TYPE_PARAMETER` nodes, generics-aware edges** — YAGNI for find-usages.
- **Incrementality / per-commit cache, cross-language API links** — separate concerns; the "fast per-commit" target is addressed in a later spec.

## 10. Known Risks / jedi Limitations

- No call-hierarchy API (we build it from primitives — handled by occurrence-driven flow).
- `get_references` is name-based, not call-aware — but we use tree-sitter to know the occurrence is a call, so this limitation does not bite the call edge.
- Dynamic dispatch (duck typing, untyped receivers, metaclasses, `getattr`) resolves to multiple candidates or none — emit all candidates as separate edges, fall back to `EXTERNAL_SYMBOL(origin=unknown)`.
- Cross-file precision depends on a correctly configured `jedi.Project` (environment / sys.path); misconfiguration degrades resolution to `unknown`.

## 11. Sources

- jedi 0.19.2 API research (Script/Project, goto/infer/get_references/get_context, Name fields, origin classification, limitations).
- Resolved code-graph schema research: SCIP `scip.proto` + DESIGN.md, LSIF 0.6.0, stack-graphs (docs.rs + arXiv:2211.01224 + ESOP 2015), Glean schema sources, LSP 3.17 — node/edge catalog with ESSENTIAL/HIGH-VALUE/YAGNI tiers.
