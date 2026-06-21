---
sidebar_position: 5
---

# Rust adapter

The Rust adapter parses `.rs` files with Tree-sitter and resolves types through
[`rust-analyzer`](https://rust-analyzer.github.io/), the official Rust language
server.

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
from graphlens_rust import RustAdapter, RustResolver, RustAnalyzerResolver
```

| Property | Value |
|---|---|
| Language id | `rust` |
| Project marker | `Cargo.toml` |
| Resolver | `RustAnalyzerResolver` |
| Engine | `rust-analyzer` (LSP) |

## Requirements

`RustAnalyzerResolver` drives a `rust-analyzer` process and needs the Rust
toolchain (Cargo) available. Both are pre-installed in the Docker image. Built
from source, ensure `rust-analyzer` is on the `PATH`; otherwise the adapter
falls back to a structure-only graph and reports a non-`ok`
[resolver status](../getting-started/concepts.md#resolver-status).

## CLI

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --lang rust
```
