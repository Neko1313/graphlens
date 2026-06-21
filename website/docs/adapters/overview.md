---
sidebar_position: 1
---

# Adapters overview

A **language adapter** is the component that turns source code into a
`GraphLens`. Each language lives in its own package and registers itself with
the core through Python entry points — the core never imports an adapter
directly.

| Language | Package | Adapter | Resolver engine |
|---|---|---|---|
| [Python](./python.md) | `graphlens-python` | `PythonAdapter` | [`ty`](https://github.com/astral-sh/ty) |
| [TypeScript](./typescript.md) | `graphlens-typescript` | `TypescriptAdapter` | TypeScript Compiler API |
| [Go](./go.md) | `graphlens-go` | `GoAdapter` | [`gopls`](https://pkg.go.dev/golang.org/x/tools/gopls) |
| [Rust](./rust.md) | `graphlens-rust` | `RustAdapter` | [`rust-analyzer`](https://rust-analyzer.github.io/) |

## The plugin system

Adapters register under the `graphlens.adapters` entry-point group:

```toml
# packages/graphlens-python/pyproject.toml
[project.entry-points."graphlens.adapters"]
python = "graphlens_python:PythonAdapter"
```

The registry discovers installed adapters automatically at runtime:

```python
from graphlens import adapter_registry

adapter_registry.available()           # ['python', 'typescript', ...]
adapter_cls = adapter_registry.load("python")
adapter = adapter_cls()
```

Adapters can also be registered manually, which is handy in tests:

```python
adapter_registry.register("python", MyPythonAdapter)
```

This is the SQLAlchemy-dialect pattern: callers depend on the registry and a
name string, never on a concrete adapter import.

## What every adapter does

Each adapter follows the same internal pipeline:

1. **Discover roots** — `find_<lang>_roots()` locates real project sub-roots, so
   a monorepo with several independent projects of the same language is modeled
   correctly.
2. **Classify imports** — before parsing, build top-level internal module names,
   third-party package names (from dependency manifests), and the language's
   standard-library set, so every `IMPORT` node gets an
   `origin` of `stdlib` / `internal` / `third_party` / `unknown`.
3. **Parse with Tree-sitter** — extract structure, exact spans, and use-site
   *occurrences*.
4. **Resolve types** — run the language's `SymbolResolver` to turn occurrences
   into real `CALLS` / `REFERENCES` / `HAS_TYPE` / `INHERITS_FROM` edges, and
   record the [resolver status](../getting-started/concepts.md#resolver-status).
5. **Emit boundaries** — detect cross-language ports (HTTP, queue, gRPC,
   Temporal) and emit `BOUNDARY` nodes with `EXPOSES`/`CONSUMES` edges.

The result is one shared `GraphLens` regardless of language.

## Common interface

Every adapter implements the `LanguageAdapter` contract:

```python
adapter.language()            # 'python'
adapter.file_extensions()     # {'.py', '.pyi'}
adapter.can_handle(root)      # True if root looks like this language's project
adapter.collect_files(root)   # source files the adapter owns
adapter.analyze(root, files=None, *, strict=False)   # -> GraphLens
```

`can_handle` is monorepo-aware: it returns `True` for a multi-language project
even when the marker file lives in a sub-directory.

## Injecting custom dependency parsers

Every adapter accepts a `dep_parsers` constructor parameter, so you can teach it
about a non-standard manifest without subclassing:

```python
from graphlens_python import PythonAdapter
adapter = PythonAdapter(dep_parsers=[MyCustomManifestParser(), *extra])
```

See [Writing an adapter](./writing-an-adapter.md) to build one from scratch.
