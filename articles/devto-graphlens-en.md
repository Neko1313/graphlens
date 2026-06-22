---
title: "graphlens: a polyglot code-analysis framework that turns your repo into a typed graph"
published: false
tags: python, staticanalysis, ai, opensource
cover_image: ""
canonical_url: ""
---

# graphlens: turn any repo into one typed graph — across Python, TypeScript, Go and Rust

Every code-intelligence tool I've ever used falls into one of two traps.

The first is the **grep-and-read loop**: you (or your AI agent) search for a name, open ten files, read around the matches, follow an import, search again. It works, but it's slow, it burns tokens, and it has no idea that the `process_order` you found in `services.py` is the *same* `process_order` that gets called from `api.py` — versus the unrelated one in `tests/`.

The second is the **single-language silo**: tools that understand Python beautifully but go blind the moment your TypeScript front end calls a Python FastAPI route. Real systems are polyglot. Your tooling usually isn't.

[**graphlens**](https://github.com/Neko1313/graphlens) is an open-source (MIT) framework built to escape both traps. It parses a source project, normalizes its structure into a shared **graph IR**, and hands you that graph to do whatever you want with — dependency analysis, navigation, dead-code detection, or feeding an LLM agent precise answers instead of file dumps.

```
Repository → Language Adapter → GraphLens (IR) → Graph Backend
```

| Layer | Responsibility |
|---|---|
| **Language Adapter** | Parses source files, produces a `GraphLens` |
| **GraphLens** | Typed nodes + directed relations — the intermediate representation |
| **Graph Backend** | Persists or queries the graph (Neo4j, in-memory, your own) |

The key design decision: **adapters are pure data producers.** They never write to a database, never touch the filesystem after reading, never run a server. The graph is the only output. That makes the whole pipeline trivially testable, cacheable, and serializable.

## 30 seconds to your first graph

```bash
pip install "graphlens-cli[python]"
graphlens analyze ./my-project
```

```text
graphlens · my-project
  nodes:      1240
  relations:  3981
  resolver:   ok

nodes by kind        relations by kind
  FUNCTION    410       CONTAINS    980
  METHOD      265       DECLARES    870
  CLASS        98       CALLS       640
  MODULE       54       REFERENCES  410
```

Or from Python:

```python
from pathlib import Path
from graphlens import adapter_registry

adapter = adapter_registry.load("python")()
graph = adapter.analyze(Path("./my-project"))

print(len(graph.nodes), "nodes,", len(graph.relations), "relations")

fn = graph.nodes_by_name("process_order")[0]
print("called by:", [n.name for n in graph.callers(fn.id)])
```

## What makes the edges *real* (and not name-matching guesses)

Most lightweight code-graph tools resolve references by name: see a call to `save()`, draw an edge to anything called `save`. That's fast and wrong — there are usually a dozen `save`s in a codebase.

graphlens splits the work in two:

1. **Tree-sitter** parses every file into a concrete syntax tree, giving exact structure and 1-based span positions. It records every *use-site* as an **occurrence** with a role (call / read / write / annotation / base).
2. A language-specific, **type-aware resolver** then answers `definition_at(file, line, col)` for each occurrence. The resolved definition becomes a real edge to the *actual* declaration node.

| Language | Resolver | Engine |
|---|---|---|
| Python | `TyResolver` | [`ty`](https://github.com/astral-sh/ty) (Astral, Rust-based) via LSP |
| TypeScript | `TsResolver` | the TypeScript Compiler API (Node subprocess) |
| Go | `GoplsResolver` | [`gopls`](https://pkg.go.dev/golang.org/x/tools/gopls) |
| Rust | `RustAnalyzerResolver` | [`rust-analyzer`](https://rust-analyzer.github.io/) |

So a `CALLS` edge points at the real function, a `HAS_TYPE` edge at the real class, an `INHERITS_FROM` edge at the real base. This is the difference between "probably related" and "is related".

### Honesty about partial failures

Type analysis can degrade — a toolchain is missing, a file doesn't type-check. Instead of silently producing a half-resolved graph, graphlens records the outcome:

```python
from graphlens import RESOLVER_STATUS_KEY
graph.metadata[RESOLVER_STATUS_KEY]   # 'ok' | 'degraded' | 'unavailable'
```

In CI you flip on `--strict` and a non-`ok` status fails the build, so an agent or dashboard never consumes a graph that's quietly incomplete.

## The graph model

**Nodes** (`PROJECT`, `MODULE`, `FILE`, `CLASS`, `METHOD`, `FUNCTION`, `PARAMETER`, `VARIABLE`, `ATTRIBUTE`, `TYPE_ALIAS`, `IMPORT`, `DEPENDENCY`, `EXTERNAL_SYMBOL`, `BOUNDARY`) are frozen dataclasses with an id, kind, qualified name, file path, span, and free-form metadata.

**Relations** are directed, typed edges:

| Kind | Meaning |
|---|---|
| `CONTAINS` / `DECLARES` | structural containment & declaration |
| `IMPORTS` / `RESOLVES_TO` | import statements and where they resolve |
| `CALLS` / `REFERENCES` / `INHERITS_FROM` / `HAS_TYPE` | resolved, type-aware edges |
| `DEPENDS_ON` | declared package dependency |
| `EXPOSES` / `CONSUMES` / `COMMUNICATES_WITH` | cross-language boundaries |

### Deterministic IDs

A node's ID is a SHA-256 hash of `project::kind::qualified_name`:

```python
from graphlens import make_node_id
make_node_id("my-project", "my.module.func", "FUNCTION")
# → the same id every scan, on every machine
```

Because the ID depends only on identity, not file position, re-scanning yields the same IDs. That's what makes `graph.diff(other)` and incremental updates work — and what makes a graph cacheable in CI.

## The feature single-language tools can't have: cross-language boundaries

This is my favorite part. Adapters emit language-agnostic **`BOUNDARY`** nodes for the interfaces a service exposes or consumes — HTTP routes, queue topics, gRPC methods, Temporal activities — with an `EXPOSES` edge (provider) or `CONSUMES` edge (consumer).

A boundary's ID is `make_boundary_id(mechanism, key)` — *no project or language in it*. HTTP paths are normalized so that `/users/1`, `/users/{user_id}` (FastAPI), `<int:id>` (Flask), and `:id` (Express) all collapse to `GET /users/{}`.

The payoff: a Python FastAPI route and a TypeScript `fetch` to the same endpoint produce the **same** boundary ID. Merge the two graphs, run `graphlens-link`, and you get `COMMUNICATES_WITH` edges spanning the language gap:

```python
from graphlens import adapter_registry
from graphlens_link import link_graph

py = adapter_registry.load("python")().analyze(python_project)
ts = adapter_registry.load("typescript")().analyze(typescript_project)

merged = py
merged.merge(ts, allow_shared=True)   # identical BOUNDARY nodes coincide
result = link_graph(merged)           # adds consumer → provider edges

print(result.relations_added, "COMMUNICATES_WITH edges added")
```

Now you can answer "which front-end calls hit this endpoint?" — a question no single-language tool can even represent.

## Five ways to use it

**As a library** — load an adapter, get a `GraphLens`, query it: callers, callees, references, neighborhoods, diffs, JSON round-trips, multi-language merges.

**From the CLI** — five subcommands cover the common workflows:

```bash
graphlens analyze ./repo --output graph.json   # index
graphlens query process_order -g graph.json --op callers
graphlens visualize ./repo                      # interactive vis.js HTML
graphlens neo4j ./repo --uri bolt://localhost:7687
graphlens mcp --graph graph.json                # serve to agents
```

**In CI** — `--strict` plus a Docker image (`ghcr.io/neko1313/graphlens`) with every adapter and toolchain pre-installed. Index on every push, publish the graph as an artifact, fail on a degraded graph.

**To LLM agents over MCP** — `graphlens mcp` exposes a saved graph as Model Context Protocol query tools (`stats`, `find`, `callers`, `callees`, `references`, `neighbors`, `boundaries`, `communicates_with`). Instead of dumping a codebase into the prompt, the agent asks precise questions and gets small structured answers — resolved edges, not best-effort text search.

**As a Neo4j export** — straight into a graph database with `UNWIND … MERGE` Cypher (no APOC required), then query it however you like.

## Plugin architecture: the SQLAlchemy-dialect pattern

The core never imports an adapter. Each language is a separate package that registers itself via Python entry points:

```toml
[project.entry-points."graphlens.adapters"]
python = "graphlens_python:PythonAdapter"
```

Callers resolve adapters through a registry, by name string:

```python
adapter_registry.available()        # ['python', 'typescript', ...]
adapter = adapter_registry.load("python")()
```

Adding a new language means writing one package against the `LanguageAdapter` contract — no changes to the core.

## What graphlens is *not*

The scope is deliberately narrow, and the docs spell it out. graphlens produces a graph IR and stops there. It does **not**:

- persist state or own a database (backends are a separate consuming layer);
- watch the filesystem or re-index incrementally on its own (scans are pure functions; deterministic IDs *enable* incremental updates, but the caller drives them);
- compute embeddings, semantic search, or relevance ranking (the graph is structural and type-aware, not a vector index);
- provide a UI or an agent runtime (`visualize` emits static HTML, `mcp` exposes query tools — neither hosts a long-running service).

Those belong to tools built *on top of* graphlens. Keeping the core minimal is what keeps it composable.

## Benchmarks

Throughput on real-world projects, refreshed on every release inside the published Docker image (single cold run, indicative):

| Project | Lang | LOC | Nodes | Time | Resolved |
|---|---|--:|--:|--:|--:|
| apache/superset | python | 399 519 | 156 251 | 148.7s | 84% |
| colinhacks/zod | typescript | 74 194 | 8 741 | 19.0s | 91% |
| gin-gonic/gin | go | 23 672 | 7 227 | 13.9s | 100% |
| gohugoio/hugo | go | 224 821 | 34 809 | 112.7s | 99% |
| BurntSushi/ripgrep | rust | 50 275 | 9 612 | 113.1s | 99% |

## Try it

```bash
pip install "graphlens-cli[python]"
graphlens analyze . --output graph.json
graphlens visualize .
```

- **Repo:** https://github.com/Neko1313/graphlens
- **Docs:** https://Neko1313.github.io/graphlens/
- **Requirements:** Python 3.13+. Python (`ty`) and TypeScript (Node) toolchains install on demand; Go and Rust adapters come via the Docker image.

If you've ever wanted a single, accurate, language-agnostic model of "how does this codebase actually fit together" — that's exactly what graphlens hands you. I'd love feedback, issues, and adapter contributions.
