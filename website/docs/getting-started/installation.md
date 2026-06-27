---
sidebar_position: 1
---

# Installation

graphlens is published to PyPI as a small **core** library plus a set of
**language adapters** and a **CLI**. You install only the pieces you need; the
core never pulls in an adapter you did not ask for.

## Requirements

- Python **3.13** or higher
- [uv](https://docs.astral.sh/uv/) is recommended but `pip` works too.

## Core library

The core contains the models, contracts, registry, exceptions, and utilities —
everything except the language-specific parsing.

```bash
pip install graphlens
```

```bash
uv add graphlens
```

## Core + a language adapter

Each adapter is exposed as an [extra](https://peps.python.org/pep-0508/#extras)
on the `graphlens` distribution:

```bash
pip install "graphlens[python]"        # Python adapter (Tree-sitter + ty)
pip install "graphlens[typescript]"    # TypeScript adapter (Tree-sitter + Compiler API)
pip install "graphlens[go]"            # Go adapter (Tree-sitter + gopls)
pip install "graphlens[rust]"          # Rust adapter (Tree-sitter + rust-analyzer)
pip install "graphlens[link]"          # cross-language linker (graphlens-link)
pip install "graphlens[all]"           # every adapter + the linker
```

The **PHP** adapter is Docker-only — it is not published to PyPI, so there is no
`graphlens[php]` extra. Use the [Docker image](#docker-all-adapters--toolchains-pre-installed),
which bundles `PhpAdapter` together with the `phpantom_lsp` binary.

With uv:

```bash
uv add "graphlens[python]"
uv add "graphlens[typescript]"
```

## The CLI

`graphlens-cli` provides the `graphlens` command (`analyze`, `query`,
`visualize`, `neo4j`). It declares its own extras so you can pick the
adapters and exporters you need:

```bash
pip install "graphlens-cli[python]"        # CLI + Python adapter
pip install "graphlens-cli[typescript]"    # CLI + TypeScript adapter
pip install "graphlens-cli[neo4j]"         # CLI + Neo4j exporter dependency
pip install "graphlens-cli[all]"           # CLI + Python + TypeScript + Neo4j
```

To serve a graph to coding agents over MCP, install the dedicated
[graphlens-mcp](https://github.com/Neko1313/graphlens-mcp) server instead —
it is built on top of this engine.

```bash
uv add "graphlens-cli[all]"
```

After installation the `graphlens` entry point is on your `PATH`:

```bash
graphlens --help
```

## Docker (all adapters + toolchains pre-installed)

The published image bundles the CLI with **every** adapter **and** the
toolchains their resolvers drive (`ty`, Node, Go + `gopls`, Rust +
`rust-analyzer`, PHP + `phpantom_lsp`). This is the supported way to get the
Go, Rust, and PHP adapters, which are not published to PyPI, and the easiest
way to run graphlens in CI with no local setup. Mount your project at
`/workspace`:

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --output /workspace/graph.json
```

The image is published to the GitHub Container Registry on each release
(`:latest` plus `:X.Y.Z` and `:X.Y` version tags). See the
[Docker guide](../ci-integration/docker.md) for details.

## What gets installed where

| Distribution | Import package | Provides |
|---|---|---|
| `graphlens` | `graphlens` | core models, contracts, registry, utils |
| `graphlens[python]` | `graphlens_python` | `PythonAdapter`, `TyResolver` |
| `graphlens[typescript]` | `graphlens_typescript` | `TypescriptAdapter`, `TsResolver` |
| `graphlens[go]` | `graphlens_go` | `GoAdapter`, `GoplsResolver` |
| `graphlens[rust]` | `graphlens_rust` | `RustAdapter`, `RustAnalyzerResolver` |
| Docker image only | `graphlens_php` | `PhpAdapter`, `PhpantomResolver` |
| `graphlens[link]` | `graphlens_link` | `link_graph`, `LinkResult` |
| `graphlens-cli` | `graphlens_cli` | the `graphlens` CLI |

## Verify the install

```python
from graphlens import adapter_registry
print(adapter_registry.available())   # e.g. ['python', 'typescript']
```

If your adapter shows up in that list, the entry point was discovered and you
are ready to go. Continue with the [Quick Start](./quick-start.md).
