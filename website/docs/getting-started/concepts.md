---
sidebar_position: 3
---

# Core Concepts

A short tour of the vocabulary you will meet everywhere in graphlens. If you
read only one page before diving in, read this one.

## The pipeline

```
Repository → Language Adapter → GraphLens (IR) → Graph Backend
```

graphlens never mixes these stages. An **adapter** turns source code into a
**graph**, and a **backend** (Neo4j, JSON on disk, your own code) consumes the
graph. The graph is the contract between them.

## GraphLens — the intermediate representation

A `GraphLens` is an in-memory container of two things:

- **nodes** — typed entities (a class, a function, an import, a project…)
- **relations** — directed, typed edges between nodes (`CALLS`, `CONTAINS`, …)

It also carries a `metadata` dict (for example the resolver status) and provides
indexed query methods so you never have to scan the lists by hand. See the
[Graph Model](../graph-model/nodes.md) section for the full node and relation
vocabularies, and the [`GraphLens` API reference](../api-reference/graphlens.md)
for every method.

## Nodes

Every node is a frozen dataclass with an `id`, a `kind`, a `qualified_name`, a
short `name`, an optional `file_path` and `span`, and a free-form `metadata`
dict. The `kind` is one of:

`PROJECT` · `MODULE` · `FILE` · `CLASS` · `METHOD` · `FUNCTION` ·
`PARAMETER` · `VARIABLE` · `ATTRIBUTE` · `TYPE_ALIAS` · `IMPORT` ·
`DEPENDENCY` · `EXTERNAL_SYMBOL` · `BOUNDARY`

[→ Node kinds in detail](../graph-model/nodes.md)

## Relations

Every relation is a directed edge `source_id → target_id` with a `kind`:

`CONTAINS` · `DECLARES` · `IMPORTS` · `RESOLVES_TO` · `CALLS` ·
`REFERENCES` · `INHERITS_FROM` · `HAS_TYPE` · `DEPENDS_ON` · `EXPOSES` ·
`CONSUMES` · `COMMUNICATES_WITH`

[→ Relation kinds in detail](../graph-model/relations.md)

## Deterministic node IDs

Node IDs are not random. They are a SHA-256 hash of
`project_name::kind::qualified_name`, truncated to 16 hex characters:

```python
from graphlens import make_node_id
make_node_id("my-project", "my.module.func", "FUNCTION")
# → same id every time you scan, on every machine
```

Because the ID depends only on identity (not position in the file), re-scanning
a project produces the same IDs — which is exactly what makes
[`diff`](../guides/library-api.md#diffing-two-graphs) and incremental updates
possible.

## Adapters

A **language adapter** implements the `LanguageAdapter` contract: it knows how
to recognize a project (`can_handle`), which file extensions it owns
(`file_extensions`), and how to turn a project root into a `GraphLens`
(`analyze`). Adapters are **pure data producers** — they never write to a
backend.

Adapters register themselves through Python entry points under the
`graphlens.adapters` group, so the core discovers them at runtime without any
imports:

```python
from graphlens import adapter_registry
adapter_registry.available()        # ['python', 'typescript', ...]
adapter = adapter_registry.load("python")()
```

[→ Adapters overview](../adapters/overview.md) ·
[→ Writing an adapter](../adapters/writing-an-adapter.md)

## Resolvers

Tree-sitter gives an adapter exact structure and span positions, but it cannot
tell you *which* `foo` a call refers to. That job belongs to a **resolver** — a
`SymbolResolver` that drives a language-specific, type-aware engine:

| Language | Resolver | Engine |
|---|---|---|
| Python | `TyResolver` | [`ty`](https://github.com/astral-sh/ty) (Astral, Rust-based) via LSP |
| TypeScript | `TsResolver` | the TypeScript Compiler API (Node subprocess) |
| Go | `GoplsResolver` | [`gopls`](https://pkg.go.dev/golang.org/x/tools/gopls) |
| Rust | `RustAnalyzerResolver` | [`rust-analyzer`](https://rust-analyzer.github.io/) |

During analysis the adapter collects every use-site as an *occurrence*, then
asks the resolver `definition_at(file, line, col)` for each one. The resolved
definition becomes a real `CALLS`, `REFERENCES`, `HAS_TYPE`, or `INHERITS_FROM`
edge to the actual declaration node — not a name-based guess.

## Resolver status

Type-aware analysis can fail partially (a toolchain is missing, a file does not
type-check). Rather than silently degrade, every adapter records how the
resolver did on the graph's metadata under `RESOLVER_STATUS_KEY`:

```python
from graphlens import RESOLVER_STATUS_KEY
graph.metadata[RESOLVER_STATUS_KEY]   # 'ok' | 'degraded' | 'unavailable'
```

| Status | Meaning |
|---|---|
| `ok` | the type-aware layer ran to completion |
| `degraded` | the resolver started but some queries failed |
| `unavailable` | the resolver never started (e.g. toolchain missing) |

Always check this before trusting `CALLS`/`HAS_TYPE` edges. In CI, the
[`--strict` flag](../ci-integration/overview.md#strict-mode) turns anything
other than `ok` into a non-zero exit so a pipeline never feeds agents an
incomplete graph.

## Import origin classification

Every `IMPORT` node carries `metadata["origin"]`, one of:

| Value | Meaning |
|---|---|
| `stdlib` | language standard library (`os`, `sys`, …) |
| `internal` | a module declared within the same project |
| `third_party` | a package listed in a dependency manifest |
| `unknown` | none of the above (transitive dep, missing, …) |

This is what lets you separate "our code" from "the ecosystem" when you walk the
graph. `EXTERNAL_SYMBOL` nodes carry the same `origin` vocabulary.

## Cross-language boundaries

Some edges cross language lines. Adapters emit language-agnostic `BOUNDARY`
nodes for the interfaces a service **exposes** or **consumes** — HTTP routes,
queue topics, gRPC methods, Temporal activities. Because a boundary's ID depends
only on `(mechanism, key)`, a Python FastAPI route and a TypeScript `fetch` call
to the same path collapse onto **one** `BOUNDARY` node when their graphs merge.
[`graphlens-link`](../guides/cross-language.md) then pairs `CONSUMES` with
`EXPOSES` to add `COMMUNICATES_WITH` edges.

## Where to go next

- [Library API](../guides/library-api.md) — put these concepts to work in Python.
- [Graph Model](../graph-model/nodes.md) — the exhaustive node/relation reference.
- [Adapters](../adapters/overview.md) — how each language is supported.
