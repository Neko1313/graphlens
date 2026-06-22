---
sidebar_position: 5
---

# Rust adapter

The Rust adapter parses `.rs` files with Tree-sitter and resolves symbols from a
[`rust-analyzer`](https://rust-analyzer.github.io/) **SCIP batch index**. The
default `RustScipResolver` runs `rust-analyzer scip` once to write a static
[SCIP](https://github.com/sourcegraph/scip) index and answers every query from
it, instead of driving an interactive LSP server that keeps the whole
workspace's analysis state resident. On a large workspace such as ruff this is
roughly `2 GB` / `70 s` versus `15 GB` / `250 s` for the LSP path — and it
produces a complete index rather than degrading partway through.

:::info Get it through Docker
The Rust adapter is **not published to PyPI**. The supported way to use it is the
[Docker image](../ci-integration/docker.md), which bundles the adapter together
with the Rust toolchain and `rust-analyzer`:

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --lang rust --output /workspace/graph.json
```
:::

## Use

```python
from pathlib import Path
from graphlens import adapter_registry

adapter = adapter_registry.load("rust")()
graph = adapter.analyze(Path("./my-crate"))
```

The package exports `RustAdapter` and its resolvers:

```python
from graphlens_rust import (
    RustAdapter,
    RustResolver,
    RustScipResolver,
    RustAnalyzerResolver,
)
```

| Property | Value |
|---|---|
| Language id | `rust` |
| Project marker | `Cargo.toml` |
| Resolver | `RustScipResolver` (default) |
| Engine | `rust-analyzer scip` (batch SCIP index) |

### Resolvers

| Resolver | Engine | Use it for |
|---|---|---|
| `RustScipResolver` (default) | `rust-analyzer scip` batch index | Whole-project scans; far lower peak memory and faster than the LSP path. |
| `RustAnalyzerResolver` | `rust-analyzer` LSP server | Available as an alternative (e.g. interactive/incremental queries via `definition_at`/`references_to`). |
| `RustResolver` | none (structure only) | Explicitly skip type-aware resolution; always reports `unavailable`. |

Inject a non-default resolver through the constructor:

```python
from graphlens_rust import RustAdapter, RustAnalyzerResolver

adapter = RustAdapter(resolver=RustAnalyzerResolver())
```

## Requirements

Both resolvers drive `rust-analyzer` and need the Rust toolchain (Cargo)
available. Both are pre-installed in the Docker image. Built from source, ensure
`rust-analyzer` is on the `PATH`; otherwise the adapter falls back to a
structure-only graph and reports a non-`ok`
[resolver status](../getting-started/concepts.md#resolver-status).

## CLI

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --lang rust
```
