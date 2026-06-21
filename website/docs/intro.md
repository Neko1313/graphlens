---
slug: /
sidebar_position: 1
---

# Introduction

**graphlens** is an extensible polyglot code analysis framework. It parses
source projects, normalizes their structure into a shared **graph IR**, and
exposes that graph for dependency analysis, navigation, and code-intelligence
tooling.

```
Repository → Language Adapter → GraphLens (IR) → Graph Backend
```

| Layer | Responsibility |
|---|---|
| **Language Adapter** | Parses source files, produces a `GraphLens` |
| **GraphLens** | Typed nodes + directed relations — the intermediate representation |
| **Graph Backend** | Persists or queries the graph (Neo4j, in-memory, your own) |

Adapters are **pure data producers** — they never write to any backend. The
graph is the only output, which makes the whole pipeline easy to test, cache,
serialize, and reason about.

## Why a graph IR?

- **Language-agnostic** — one shared model for Python, TypeScript, Go, and Rust.
- **Plugin-based adapters** — each language is a separate package, discovered at
  runtime through Python entry points. The core never imports an adapter.
- **Tree-sitter powered** — every adapter uses Tree-sitter for structure and
  exact span positions, combined with a type-aware resolver
  (`ty` for Python, the TypeScript Compiler API, `gopls` for Go,
  `rust-analyzer` for Rust) that emits real `CALLS` / `REFERENCES` /
  `HAS_TYPE` / `INHERITS_FROM` edges.
- **Cross-language aware** — adapters emit language-agnostic `BOUNDARY` ports
  (HTTP, queues, gRPC, Temporal), and [`graphlens-link`](./guides/cross-language.md)
  connects a consumer in one language to a provider in another.
- **Monorepo aware** — root discovery handles multi-language repositories
  correctly.
- **Deterministic node IDs** — a SHA-256 hash of `project::kind::qualified_name`
  is stable across re-scans, which makes diffing and incremental updates work.

## What can you do with it?

<div className="row">
  <div className="col col--6">

**Use it as a library**

Call an adapter from Python, get a `GraphLens`, and query it — find callers and
callees, walk neighborhoods, diff two scans, serialize to JSON, or merge graphs
from several languages.

[→ Library API guide](./guides/library-api.md)

  </div>
  <div className="col col--6">

**Use it from the CLI**

`graphlens analyze`, `query`, `visualize`, `neo4j`, and `mcp` cover the common
workflows without writing any code.

[→ CLI guide](./guides/cli.md)

  </div>
</div>

<div className="row">
  <div className="col col--6">

**Run it in CI**

A `--strict` mode and a pre-built Docker image with every toolchain make it
easy to index a repository on every push and fail the build on a degraded
graph.

[→ CI integration](./ci-integration/overview.md)

  </div>
  <div className="col col--6">

**Serve it to agents**

The `mcp` command exposes a saved graph to LLM agents over the Model Context
Protocol as a set of query tools.

[→ MCP server](./guides/mcp-server.md)

  </div>
</div>

## A 30-second taste

```python
from pathlib import Path
from graphlens import adapter_registry

adapter = adapter_registry.load("python")()
graph = adapter.analyze(Path("./my-project"))

print(len(graph.nodes), "nodes,", len(graph.relations), "relations")

fn = graph.nodes_by_name("my_function")[0]
print("called by:", [n.name for n in graph.callers(fn.id)])
```

## Requirements

- **Python 3.13+**
- [uv](https://docs.astral.sh/uv/) is recommended for installation and development.
- Language resolvers drive external toolchains. The Python (`ty`) and TypeScript
  (Node) toolchains are installed on demand; Go (`gopls`) and Rust
  (`rust-analyzer`) are most easily obtained through the
  [Docker image](./ci-integration/docker.md).

## Next steps

- [Installation](./getting-started/installation.md) — install the core, an adapter, or the CLI.
- [Quick Start](./getting-started/quick-start.md) — analyze a project in a few lines.
- [Core Concepts](./getting-started/concepts.md) — adapters, resolvers, nodes, relations, and boundaries.
