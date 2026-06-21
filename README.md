<div align="center">

  <h1>graphlens</h1>

  <p>Extensible polyglot code analysis framework that parses source projects, normalizes their structure into a shared graph IR, and exposes it for dependency analysis, navigation, and code intelligence tooling.</p>

  [![PyPI](https://img.shields.io/pypi/v/graphlens?color=blue)](https://pypi.org/project/graphlens/)
  [![Python](https://img.shields.io/pypi/pyversions/graphlens)](https://pypi.org/project/graphlens/)
  [![License](https://img.shields.io/github/license/Neko1313/graphlens)](LICENSE)
  [![CI](https://img.shields.io/github/actions/workflow/status/Neko1313/graphlens/ci.yml?label=CI)](https://github.com/Neko1313/graphlens/actions)
  [![codecov](https://codecov.io/gh/Neko1313/graphlens/graph/badge.svg?token=n3oRe180jg)](https://codecov.io/gh/Neko1313/graphlens)

  [Documentation](https://Neko1313.github.io/graphlens/) · [Repository](https://github.com/Neko1313/graphlens) · [Issues](https://github.com/Neko1313/graphlens/issues)

</div>

---

## Architecture

```
Repository → Language Adapter → GraphLens (IR) → Graph Backend
```

| Layer | Responsibility |
|---|---|
| **Language Adapter** | Parses source files, produces `GraphLens` |
| **GraphLens** | Typed nodes + directed relations (the IR) |
| **Graph Backend** | Persists or queries the graph (Neo4j, in-memory, …) |

Adapters are **pure data producers** — they never write to any backend. The graph is the only output.

## Why graph IR?

- **Language-agnostic** — one shared model for Python, TypeScript, Rust, …
- **Plugin-based adapters** — each language is a separate package, registered via Python entry points
- **Tree-sitter powered** — all adapters use tree-sitter for CST parsing and exact span positions, combined with type-aware resolution (ty for Python, TypeScript Compiler API for TypeScript, gopls for Go, rust-analyzer for Rust)
- **Cross-language aware** — adapters emit language-agnostic `BOUNDARY` ports (HTTP, queues, gRPC, Temporal); `graphlens-link` connects a consumer in one language to a provider in another
- **Monorepo aware** — `can_handle()` and `find_*_roots()` handle multi-language repos correctly
- **Deterministic node IDs** — SHA-256 hash of `project::kind::qualified_name` → stable across re-scans

## Documentation

Full product documentation lives at **<https://Neko1313.github.io/graphlens/>**
(built with Docusaurus from [`website/`](website/)):

- [Getting Started](https://Neko1313.github.io/graphlens/docs/getting-started/installation) — install, quick start, core concepts
- [Guides](https://Neko1313.github.io/graphlens/docs/guides/library-api) — library API, CLI, querying, visualization, Neo4j, cross-language, MCP
- [CI Integration](https://Neko1313.github.io/graphlens/docs/ci-integration/overview) — strict mode, GitHub Actions, Docker, local hooks
- [Adapters](https://Neko1313.github.io/graphlens/docs/adapters/overview) — Python, TypeScript, Go, Rust, and writing your own
- [Graph Model](https://Neko1313.github.io/graphlens/docs/graph-model/nodes) — nodes, relations, boundaries, serialization
- [API Reference](https://Neko1313.github.io/graphlens/docs/api-reference/graphlens) — exact signatures

To run the docs locally: `cd website && pnpm install && pnpm start`.

## Installation

```bash
# Core library only (models, contracts, registry)
pip install graphlens

# Core + Python adapter
pip install "graphlens[python]"

# Core + TypeScript adapter
pip install "graphlens[typescript]"

# Core + Go / Rust adapters
pip install "graphlens[go]"
pip install "graphlens[rust]"

# CLI (graphlens analyze / visualize / query / neo4j)
pip install "graphlens-cli[python]"          # with Python adapter
pip install "graphlens-cli[all]"             # Python + TS + Go + Rust + Neo4j
```

With uv:

```bash
uv add graphlens
uv add "graphlens[python]"
uv add "graphlens[typescript]"
uv add "graphlens-cli[all]"
```

### Docker (all adapters + toolchains pre-installed)

For CI, the published image bundles the CLI with every adapter **and** the
toolchains their resolvers drive (ty, Node, Go + gopls, Rust + rust-analyzer)
— no local setup required, and the supported way to get the Go and Rust
adapters (which are not published to PyPI). Mount your project at
`/workspace`:

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --output /workspace/graph.json
```

The image is published to the GitHub Container Registry on each release
(`:latest` plus `:X.Y.Z` / `:X.Y` version tags).

## Quick start

```python
from pathlib import Path
from graphlens import adapter_registry

# Load and instantiate the Python adapter
adapter = adapter_registry.load("python")()

# Analyze a project — returns a GraphLens
graph = adapter.analyze(Path("./my-project"))

print(f"Nodes:     {len(graph.nodes)}")
print(f"Relations: {len(graph.relations)}")

# Inspect nodes by kind
from graphlens import NodeKind

modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]

# Check the resolver actually ran (don't trust a silently degraded graph)
from graphlens import RESOLVER_STATUS_KEY
assert graph.metadata[RESOLVER_STATUS_KEY] == "ok"

# Query the graph (indexed lookups, no manual scanning)
fn = next(n for n in graph.nodes.values() if n.name == "my_function")
callers = graph.callers(fn.id)          # who calls it
callees = graph.callees(fn.id)          # what it calls
near = graph.neighbors(fn.id, depth=2)  # 2-hop neighbourhood

# Serialize for pipelines / agents (round-trippable JSON), then reload
text = graph.to_json(indent=2)
graph2 = type(graph).from_json(text)

# Diff two scans (e.g. before/after a change)
diff = old_graph.diff(graph)
print(diff.added_nodes, diff.removed_relations, diff.is_empty)
```

## CLI (`graphlens-cli`)

Install `graphlens-cli` to get the `graphlens` entry point:

```bash
# Print node/relation statistics
graphlens analyze <project_root>
graphlens analyze ~/myrepo --lang python,typescript,go,rust

# Serialize the graph to JSON (CI indexing step); --strict fails on a
# degraded resolver so a pipeline never feeds agents an incomplete graph
graphlens analyze ~/myrepo --output graph.json
graphlens analyze ~/myrepo --format json
graphlens analyze ~/myrepo --strict

# Query a saved graph (callers | callees | references | neighbors)
graphlens query my_function --graph graph.json --op callers
graphlens query MyClass.method --graph graph.json --op neighbors --depth 2

# Interactive HTML graph viewer (opens in browser)
graphlens visualize <project_root>
graphlens visualize ~/myrepo --lang python --show-external --max-nodes 500
graphlens visualize . --output graph.html --no-open

# Export to Neo4j
graphlens neo4j <project_root> --uri bolt://localhost:7687 --user neo4j --password secret
graphlens neo4j . --wipe --batch-size 200

# Serve the graph to agents over the Model Context Protocol (needs the
# optional `mcp` extra: pip install "graphlens-cli[mcp]")
graphlens mcp --graph graph.json
```

### `mcp` — Model Context Protocol server

Exposes a saved graph to LLM agents as MCP tools: `graph_stats`,
`find_nodes`, `callers`, `callees`, `references`, `neighbors`,
`boundaries`, and `communicates_with`. Install with the `mcp` extra and
point it at a JSON graph produced by `graphlens analyze --output`.

### `visualize` — interactive HTML graph viewer

Produces a self-contained HTML file powered by vis.js and opens it in the browser.

| Flag | Description |
|---|---|
| `--lang auto\|python\|typescript\|python,typescript` | Adapters to use (default: auto-detect all) |
| `--show-external` | Include stdlib / third-party external symbol nodes |
| `--show-structure` | Add `CONTAINS` / `DECLARES` structural edges |
| `--max-nodes N` | Prune low-degree nodes above N (default: 1500) |
| `--output PATH` | Write HTML to PATH instead of `graph-<name>.html` |
| `--no-open` | Do not open the browser automatically |

**Click behaviour** — click any node to see its info panel. For `FUNCTION`
and `METHOD` nodes the panel has a **"Show callers"** button that switches the
graph into focus mode: only the selected node and every node that calls or
references it are shown, with the caller list in the sidebar. Click empty
space or **← Back** to return to the full graph.

### `neo4j` — export to Neo4j

Uses `UNWIND … MERGE` Cypher (no APOC required). Every node gets a `:Code`
label plus a kind-specific label (`:Function`, `:ExternalSymbol`, …).
Relations are created grouped by type. Install the optional `neo4j` extra:

```bash
pip install "graphlens-cli[neo4j]"
```

## Graph model

### Node kinds

| Kind | Description |
|---|---|
| `PROJECT` | Root project node |
| `MODULE` | Python/TS/… module (directory or file) |
| `FILE` | Source file |
| `CLASS` | Class declaration |
| `FUNCTION` | Top-level function |
| `METHOD` | Method inside a class |
| `PARAMETER` | Function/method parameter |
| `VARIABLE` | Module-level or local variable |
| `ATTRIBUTE` | Class attribute |
| `TYPE_ALIAS` | Type alias declaration |
| `IMPORT` | Import statement |
| `DEPENDENCY` | Declared package dependency |
| `EXTERNAL_SYMBOL` | External symbol (stdlib, third-party, or unknown); carries `metadata["origin"]` |
| `BOUNDARY` | Cross-language interface port (HTTP route, queue topic, gRPC method, Temporal activity); shared id collapses matching server/client across languages |

### Relation kinds

| Kind | Description |
|---|---|
| `CONTAINS` | Structural containment (project → module → file → class) |
| `DECLARES` | Declaration (file declares function, class declares method) |
| `IMPORTS` | Import edge (file → import node) |
| `RESOLVES_TO` | Import resolved to a module or external symbol |
| `CALLS` | Function/method call (resolved to declaration node) |
| `REFERENCES` | Value reference (variable/attribute used as a value) |
| `INHERITS_FROM` | Class inheritance (resolved to declaration node) |
| `HAS_TYPE` | Type annotation/inference edge (function/param/variable → class or external) |
| `DEPENDS_ON` | Package dependency |
| `EXPOSES` | A server/provider exposes a `BOUNDARY` (e.g. an HTTP route handler) |
| `CONSUMES` | A client/consumer consumes a `BOUNDARY` (e.g. an HTTP call) |
| `COMMUNICATES_WITH` | Consumer → provider, added by `graphlens-link` from matching `EXPOSES`/`CONSUMES` |

### Cross-language boundaries

Adapters emit `BOUNDARY` ports for the interfaces a service exposes or
consumes — HTTP/REST routes and clients, message-queue topics, gRPC
methods, and Temporal activities. Each port has a language-agnostic id
(`make_boundary_id(mechanism, key)`), so a Python FastAPI route and a
TypeScript `fetch` call to the same path collapse onto **one** `BOUNDARY`
node when their graphs are merged. The `graphlens-link` package then pairs
`CONSUMES` with `EXPOSES` into `COMMUNICATES_WITH` edges:

```python
from graphlens_link import link_graph

merged = python_graph.merge(ts_graph, allow_shared=True)
result = link_graph(merged)          # adds COMMUNICATES_WITH edges
```

See `examples/demo_cross_language.py` for a Python-server ↔ TypeScript-client
walkthrough.

## Adapter plugin system

Language adapters register themselves via Python entry points — no changes to the core needed:

```toml
# packages/graphlens-python/pyproject.toml
[project.entry-points."graphlens.adapters"]
python = "graphlens_python:PythonAdapter"
```

The registry discovers installed adapters automatically at runtime:

```python
from graphlens import adapter_registry

adapter_registry.available()          # ["python", ...]
adapter_cls = adapter_registry.load("python")
adapter = adapter_cls()
```

Adapters can also be registered manually (useful for testing):

```python
adapter_registry.register("python", MyPythonAdapter)
```

## Implementing an adapter

Subclass `LanguageAdapter` and implement four methods:

```python
from pathlib import Path
from graphlens import GraphLens, LanguageAdapter

class MyLangAdapter(LanguageAdapter):
    def language(self) -> str:
        return "mylang"

    def file_extensions(self) -> set[str]:
        return {".ml", ".mli"}

    def can_handle(self, project_root: Path) -> bool:
        return (project_root / "dune-project").exists()

    def analyze(
        self, project_root: Path, files: list[Path] | None = None
    ) -> GraphLens:
        graph = GraphLens()
        files = files or self.collect_files(project_root)
        # ... parse and populate graph ...
        return graph
```

Register in `pyproject.toml` and the core registry finds it automatically.

## Project structure

```
graphlens/                      ← uv workspace root (core library)
  src/graphlens/                ← models, contracts, registry, exceptions, utils
  packages/
    graphlens-python/           ← Python adapter (tree-sitter + ty)
    graphlens-typescript/       ← TypeScript adapter (tree-sitter + Compiler API)
    graphlens-go/               ← Go adapter (tree-sitter + gopls)
    graphlens-rust/             ← Rust adapter (tree-sitter + rust-analyzer)
    graphlens-link/             ← cross-language linker (COMMUNICATES_WITH)
    graphlens-cli/              ← CLI (typer): analyze, query, visualize, neo4j, mcp
  tests/                         ← core tests (100% coverage)
  examples/                      ← standalone usage examples
```

## Development

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), [task](https://taskfile.dev/).

```bash
task install        # uv sync --all-groups
task lint           # ruff + ty + bandit for all packages
task tests          # all tests with coverage
```

Individual package tasks:

```bash
task core:lint           task core:test
task python:lint         task python:test
task typescript:lint     task typescript:test
task cli:lint            task cli:test
```

## License

MIT
