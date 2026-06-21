---
sidebar_position: 4
---

# Go adapter

The Go adapter parses `.go` files with Tree-sitter and resolves types through
[`gopls`](https://pkg.go.dev/golang.org/x/tools/gopls), the official Go language
server.

:::info Get it through Docker
The Go adapter is **not published to PyPI**. The supported way to use it is the
[Docker image](../ci-integration/docker.md), which bundles the adapter together
with Go and `gopls`:

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --lang go --output /workspace/graph.json
```
:::

## Use

```python
from pathlib import Path
from graphlens import adapter_registry

adapter = adapter_registry.load("go")()
graph = adapter.analyze(Path("./my-service"))
```

The package exports `GoAdapter` and its resolvers:

```python
from graphlens_go import GoAdapter, GoResolver, GoplsResolver
```

| Property | Value |
|---|---|
| Language id | `go` |
| Project marker | `go.mod` |
| Resolver | `GoplsResolver` |
| Engine | `gopls` (LSP) |

## Requirements

`GoplsResolver` drives a `gopls` process, which in turn needs a working Go
toolchain. Both are pre-installed in the Docker image. If you build the adapter
from source instead, make sure `go` and `gopls` are on the `PATH`; otherwise the
adapter falls back to a structure-only graph and reports a non-`ok`
[resolver status](../getting-started/concepts.md#resolver-status).

## CLI

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --lang go
```
