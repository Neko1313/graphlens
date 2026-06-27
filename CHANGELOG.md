# Changelog

All notable changes to this project will be documented in this file.

## [0.7.1] - 2026-06-27
### Bug Fixes

- build docs by @Neko1313

### Refactoring

- **cli**: drop built-in mcp server in favor of standalone graphlens-mcp by @Neko1313




**Full Changelog**: https://github.com/Neko1313/graphlens/compare/v0.7.0...v0.7.1
## [0.7.0] - 2026-06-23
### Bug Fixes

- **php**: fix(php): stream didOpen from a writer thread to avoid pipe deadlock by @Neko1313 in [#34](https://github.com/Neko1313/graphlens/pull/34)

### Documentation

- docs: add explicit Scope &amp; Non-goals boundary by @Neko1313 in [#24](https://github.com/Neko1313/graphlens/pull/24)
- **bench**: refresh benchmark metrics for latest [skip ci] by @github-actions[bot]
- **bench**: refresh benchmark metrics for latest [skip ci] by @github-actions[bot]
- **php**: docs(php): surface PHP across the docs alongside Python/TypeScript/Go/Rust by @Neko1313 in [#35](https://github.com/Neko1313/graphlens/pull/35)

### Features

- **php**: feat(php): add PHP language adapter by @Neko1313 in [#31](https://github.com/Neko1313/graphlens/pull/31)
- **php**: feat(php): PHPantom as the sole PHP resolver with pipelined LSP batch by @Neko1313 in [#33](https://github.com/Neko1313/graphlens/pull/33)

### Performance

- **rust**: perf(rust): default to a rust-analyzer SCIP batch resolver by @Neko1313 in [#26](https://github.com/Neko1313/graphlens/pull/26)




**Full Changelog**: https://github.com/Neko1313/graphlens/compare/v0.6.1...v0.7.0
## [0.6.1] - 2026-06-21
### Bug Fixes

- **rust**: fix(rust): single workspace-rooted rust-analyzer + wait for workspace load by @Neko1313 in [#20](https://github.com/Neko1313/graphlens/pull/20)
- **rust**: fix(rust): lighten rust-analyzer workspace load + surface load failures (ruff 0%) by @Neko1313 in [#21](https://github.com/Neko1313/graphlens/pull/21)
- **rust**: fix(rust): spawn the real rust-analyzer binary, bypassing the rustup proxy (ruff 0%) by @Neko1313 in [#22](https://github.com/Neko1313/graphlens/pull/22)
- **rust**: fix(rust): analyse with the project-pinned rust-analyzer + ship it in the image (ruff 0%) by @Neko1313 in [#23](https://github.com/Neko1313/graphlens/pull/23)

### Documentation

- **bench**: refresh benchmark metrics for latest [skip ci] by @github-actions[bot]
- **bench**: refresh benchmark metrics for latest [skip ci] by @github-actions[bot]
- **bench**: refresh benchmark metrics for latest [skip ci] by @github-actions[bot]
- **bench**: refresh benchmark metrics for latest [skip ci] by @github-actions[bot]




**Full Changelog**: https://github.com/Neko1313/graphlens/compare/v0.6.0...v0.6.1
## [0.6.0] - 2026-06-21
### Bug Fixes

- **ci**: fix(ci): publish the CLI image on release and manual dispatch by @Neko1313 in [#16](https://github.com/Neko1313/graphlens/pull/16)

### Documentation

- **bench**: refresh benchmark metrics for latest [skip ci] by @github-actions[bot]
- **bench**: refresh benchmark metrics for latest [skip ci] by @github-actions[bot]
- **meta**: docs(meta): enrich PyPI package metadata for discoverability by @Neko1313 in [#19](https://github.com/Neko1313/graphlens/pull/19)
- **bench**: refresh benchmark metrics for latest [skip ci] by @github-actions[bot]

### Features

- **benchmarks**: feat(benchmarks): release-time load benchmark on real-world projects by @Neko1313 in [#15](https://github.com/Neko1313/graphlens/pull/15)
- **go,rust**: feat(go,rust): default to semantic resolvers (gopls / rust-analyzer) by @Neko1313 in [#17](https://github.com/Neko1313/graphlens/pull/17)

### Performance

- **rust,go,python,ts**: perf(rust,go): batch+pipeline LSP resolution and record resolver metrics by @Neko1313 in [#18](https://github.com/Neko1313/graphlens/pull/18)




**Full Changelog**: https://github.com/Neko1313/graphlens/compare/v0.5.0...v0.6.0
## [0.5.0] - 2026-06-21
### Features

- feat: graph IR + Go/Rust LSP semantics, cross-language boundaries, MCP, CLI, Docker & docs by @Neko1313 in [#12](https://github.com/Neko1313/graphlens/pull/12)




**Full Changelog**: https://github.com/Neko1313/graphlens/compare/v0.4.0...v0.5.0
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
