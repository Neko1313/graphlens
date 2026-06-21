# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]
### Features

- **core**: serialize a graph to/from JSON (`GraphLens.to_json` /
  `from_json` / `to_dict` / `from_dict`), round-trippable including `Span`
  metadata, with `SerializationError` on unsupported `schema_version` and
  forward-compatible unknown-field handling (TCK-1).
- **core**: indexed query API — `outgoing`/`incoming`, `callers`,
  `callees`, `references_to`, `neighbors`, `nodes_by_kind`/`_in_file`/
  `_by_name`, and `subgraph` — backed by lazily-built edge indices (TCK-2).
- **core**: `ResolverStatus` (`ok`/`degraded`/`unavailable`) plus a
  `graph.metadata["resolver_status"]` field so adapters report a truthful
  resolution state instead of silently degrading; `SymbolResolver.status()`
  contract method (TCK-3).
- **core**: deterministic `GraphLens.diff` → `GraphDiff` (added/removed/
  changed nodes and relations), order-independent (TCK-4).
- **adapters**: `analyze()` accepts `str | Path` (resolved to absolute,
  fixing the relative-path resolver-startup failure) and a `strict` flag
  that raises `AdapterError` rather than returning a degraded graph (TCK-3).
- **go**: new `graphlens-go` adapter — structural graph + go.mod
  dependency/import-origin classification + monorepo discovery (TCK-9).
- **rust**: new `graphlens-rust` adapter — structural graph + Cargo.toml
  parsing + workspace-aware crate discovery (TCK-10).
- **cli**: `analyze --format json` / `--output PATH` serialization,
  `analyze --strict` exit code, and a `query` subcommand over a saved graph
  (callers/callees/references/neighbors); `--lang go`/`rust` and
  auto-detect (TCK-11).
- **adapters**: declarative tree-sitter query helpers (`_queries.run_query`,
  cached `Query`/`QueryCursor`) now back pattern matching instead of
  hand-written traversals (TCK-5).
- **core**: cross-language boundary model — a language-agnostic `BOUNDARY`
  node plus `EXPOSES` / `CONSUMES` / `COMMUNICATES_WITH` relations, the
  `BoundaryRef` contract, `make_boundary_id`, and a shared
  `normalize_http_path`; boundaries with identical ids collapse on
  `GraphLens.merge(..., allow_shared=True)` so a server and a client in
  different languages meet at one node (TCK-6).
- **adapters**: cross-language boundary extraction for Python, TypeScript,
  Go, and Rust — HTTP/REST routes and clients (all four), message-queue
  producers/consumers (all four), Temporal activities (Python, Go), and
  gRPC services/stubs (Python, Go, Rust). Each adapter accepts a
  `boundary_extractors` constructor parameter for custom overrides (TCK-6).
- **link**: new `graphlens-link` package — `link_graph()` pairs each
  `CONSUMES` with the matching `EXPOSES` on a boundary and emits a
  `COMMUNICATES_WITH` edge (consumer → provider), idempotently, with a
  `min_confidence` filter (TCK-6).
- **cli**: `mcp` subcommand — a Model Context Protocol server exposing the
  graph query API (stats, find, callers/callees/references, neighbors,
  boundaries, communicates-with) to agents, behind an optional `mcp` extra
  (TCK-7).
- **go**: `GoplsResolver` — a gopls LSP-backed `SymbolResolver`. The visitor
  collects call and struct/interface-embedding occurrences and the adapter
  resolves them to CALLS and INHERITS_FROM edges from real cross-file
  definitions; the structure-only `GoResolver` is kept as an injectable
  fallback (TCK-12).
- **rust**: `RustAnalyzerResolver` — a rust-analyzer LSP-backed
  `SymbolResolver`; the structure-only `RustResolver` is kept as an
  injectable fallback (TCK-12).
- **examples**: `demo_cross_language.py` merges a Python server graph with a
  TypeScript client graph and runs `link_graph` to print the resolved
  `COMMUNICATES_WITH` edges.
- **docker**: a published GHCR image (`ghcr.io/neko1313/graphlens`) bundling
  the CLI, every adapter, and the toolchains their resolvers drive (ty, Node,
  Go + gopls, Rust + rust-analyzer) so projects can run the full analysis in
  CI with `docker run … analyze /workspace`; built from source and published
  on each release, with a build-only check on pull requests that touch it.

### Bug Fixes

- **python**: `analyze(str)` no longer crashes; relative roots resolve so
  the ty LSP starts instead of silently producing an import-only graph.
- **rust**: extract items inside inline `mod foo { ... }` blocks (functions,
  types, impls, `use` imports, calls) instead of silently dropping them —
  idiomatic modules such as `#[cfg(test)] mod tests` are no longer invisible.
- **typescript**: `fetch(url, {method: "..."})` is keyed by its real HTTP
  method instead of always `GET`, so non-GET calls link to the right route;
  `app.get("view engine")`-style settings getters no longer register as routes.
- **go**: `go.mod` parsing no longer captures the `require (` block opener as a
  bogus `(` dependency; generic interface type-sets (`interface { A | B }`) are
  no longer mis-modeled as embedding/`INHERITS_FROM`; `_package_qname` no longer
  raises for a file passed outside the module root.
- **rust**: a reqwest-style `.get("/path")` on a gRPC client variable is no
  longer double-counted as a spurious gRPC RPC boundary.
- **go/rust**: `internal` imports now resolve `RESOLVES_TO` the real `MODULE`
  node when present (falling back to `EXTERNAL_SYMBOL`), per the import-origin
  contract; LSP resolvers drain `publishDiagnostics` after `didOpen` and
  resolve symlinked paths so internal definitions classify correctly.
- **core**: `GraphDiff` keys relations by metadata too, so duplicate call-site
  edges and metadata-only edge changes are no longer lost; `normalize_http_path`
  only collapses `:param` at a segment start, preserving literal colons
  (`/v1/users/123:activate`); `merge` keeps the worst `resolver_status` instead
  of letting the last graph win.
- **link**: cross-language edges dedupe per boundary, so two distinct contracts
  between the same consumer/provider pair both produce a `COMMUNICATES_WITH`.
- **cli**: a foreign `resolver_status` is parsed leniently instead of crashing
  `analyze`; MCP tools expose typed schemas (e.g. `neighbors` `depth: int`).

### Dependencies

- **python**: pin `ty==0.0.26` (pre-1.0; 0.0.x can change LSP behaviour)
  for reproducible analysis (TCK-8).

## [0.4.0] - 2026-06-20
### Bug Fixes

- **python**: correct self.attr ATTRIBUTE naming; ruff + test fixes
- **types**: satisfy ty on SpanIndex narrowing and frozen-DTO test pragmas
- **python**: include project root as source root in src-layout so files outside src/ (tests, scripts) are analyzed
- **python**: read only the value of a keyword argument, not the keyword name
- **typescript**: include project root in src-layout; absolute structural file_path
- **typescript**: record module/class-scope expression statements; param name_span; class-field enclosing
- **typescript**: resolve tsconfig path aliases and stop root-config polluting internal origin
- **typescript**: classify scoped npm packages (@scope/pkg) as third_party
- **python**: declare ty as explicit dependency in pyproject.toml
- **cli**: add ty.toml with neo4j optional-import override

### CI/CD

- add ci-cli.yml workflow for graphlens-cli package

### Documentation

- **spec**: graph resolution redesign — model + resolver contract + Python(jedi)
- **plan**: implementation plan for graph resolution redesign (Python)
- **python**: document resolver redesign; export JediResolver; changelog
- **python**: document resolver redesign; export JediResolver; changelog
- clarify §9 resolution pass uses definition_at for all roles
- document infer_type_at deferral in spec §9; update CHANGELOG
- **examples**: include REFERENCES (value-uses/DI) in find-usages demo
- **spec**: typescript resolution redesign — Compiler API + Node batch bridge
- **plan**: implementation plan for typescript resolution redesign
- **typescript**: changelog + CLAUDE note; ship bridge assets; export TsResolver
- update README, CLAUDE.md, CHANGELOG for graphlens-cli and TyResolver

### Features

- **model**: add VARIABLE/ATTRIBUTE/TYPE_ALIAS nodes and HAS_TYPE relation
- **utils**: add SpanIndex location-to-node bridge
- **contracts**: add SymbolResolver ABC + ResolvedRef/Occurrence DTOs
- **python**: add jedi-backed JediResolver implementing SymbolResolver
- **python**: record name_span on structural nodes
- **python**: collect call occurrences, drop SYMBOL nodes
- **python**: collect base + annotation occurrences
- **python**: model variables/attributes/type-aliases + read/write occurrences
- **python**: occurrence-driven resolution pass — resolved CALLS/REFERENCES/HAS_TYPE/INHERITS_FROM
- **python**: record module-level and class-body calls; harden external qname fallback
- **python**: model function/variable used as a value (call args, Depends, decorator args) as REFERENCES
- **typescript**: add TsResolver (Node bridge, batch, install-on-demand)
- **typescript**: add ts_resolver.js Compiler API bridge
- **typescript**: record name_span on structural nodes
- **typescript**: collect call/read occurrences via unified scan
- **typescript**: collect base + annotation occurrences
- **typescript**: model type aliases/enums/variables + value occurrences
- **typescript**: occurrence-driven batch resolution pass
- **python**: replace JediResolver with TyResolver (ty server LSP)
- **examples**: add neo4j_export.py — export graphlens graph to Neo4j
- **cli**: add graphlens-cli package with typer-based CLI

### Miscellaneous

- **typescript**: remove dead bridge asset and annotate forward-compat guards

### Testing

- **utils**: cover SpanIndex manual build; document add_full/add_name
- **python**: cover JediResolver.infer_type_at and references_to
- **python**: assert EXTERNAL_SYMBOL fallback and return-read occurrence
- **typescript**: cover visitor occurrence branches to 100%
- **typescript**: tighten weakest assertions; add call-position checks




**Full Changelog**: https://github.com/Neko1313/graphlens/compare/v0.3.0...v0.4.0
## [0.3.0] - 2026-05-10
### Features

- **adapters**: support nested same-language monorepo roots by @Neko1313




**Full Changelog**: https://github.com/Neko1313/graphlens/compare/v0.2.2...v0.3.0
## [0.2.2] - 2026-04-01
### Bug Fixes

- **CI/CD**: publish pkg typescript




**Full Changelog**: https://github.com/Neko1313/graphlens/compare/v0.2.1...v0.2.2
## [0.2.1] - 2026-04-01
### Miscellaneous

- update examples


### New Contributors

- @ made their first contribution



**Full Changelog**: https://github.com/Neko1313/graphlens/compare/v0.2.0...v0.2.1
## [0.2.0] - 2026-03-29
### Features

- feat: add typescript adapter by @Neko1313 in [#6](https://github.com/Neko1313/graphlens/pull/6)

### Miscellaneous

- chore: add skill by @Neko1313 in [#5](https://github.com/Neko1313/graphlens/pull/5)




**Full Changelog**: https://github.com/Neko1313/graphlens/compare/v0.1.1...v0.2.0
## [0.1.1] - 2026-03-28
### Bug Fixes

- rename project by @Neko1313




**Full Changelog**: https://github.com/Neko1313/graphlens/compare/v0.1.0...v0.1.1
## [0.1.0] - 2026-03-28
### Bug Fixes

- settings by @Neko1313


### New Contributors

- @github-actions[bot] made their first contribution
- @Neko1313 made their first contribution


<!-- generated by git-cliff -->
