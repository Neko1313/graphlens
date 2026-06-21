---
sidebar_position: 2
---

# Python adapter

The Python adapter parses `.py` and `.pyi` files with Tree-sitter and resolves
types with [`ty`](https://github.com/astral-sh/ty), Astral's Rust-based type
checker.

## Install

```bash
pip install "graphlens[python]"
# or, with the CLI
pip install "graphlens-cli[python]"
```

## Use

```python
from pathlib import Path
from graphlens import adapter_registry

adapter = adapter_registry.load("python")()
graph = adapter.analyze(Path("./my-project"))
```

The package exports `PythonAdapter` and its resolver `TyResolver`:

```python
from graphlens_python import PythonAdapter, TyResolver
```

| Property | Value |
|---|---|
| Language id | `python` |
| File extensions | `.py`, `.pyi` |
| Project marker | `pyproject.toml` |
| Resolver | `TyResolver` |
| Engine | `ty` (LSP subprocess) |

## How resolution works

`TyResolver` spawns a `ty server` LSP subprocess. Files are opened lazily on the
first query per file, and `open_file()` drains `publishDiagnostics` before
returning, so definition queries never block on background analysis. For each
use-site occurrence the adapter collected, the resolver answers
`definition_at(file, line, col)`; the resolved definition becomes a real
`CALLS` / `REFERENCES` / `HAS_TYPE` / `INHERITS_FROM` edge to the actual
declaration node.

If `ty` is unavailable the adapter still produces a structure-only graph and
records the [resolver status](../getting-started/concepts.md#resolver-status) as
`unavailable` or `degraded` — check it (or use `--strict`) before trusting the
type-aware edges.

## Dependency classification

The adapter reads your dependency manifests to classify third-party imports.
Standard-library modules are recognized via `sys.stdlib_module_names`, internal
modules from your source layout, and third-party packages from the project's
manifests (including dev/test groups, so test imports classify as
`third_party`). Every `IMPORT` node ends up with `metadata["origin"]` set to
`stdlib`, `internal`, `third_party`, or `unknown`.

To support a non-standard manifest, inject parsers:

```python
adapter = PythonAdapter(dep_parsers=[MyManifestParser(), *defaults])
```

## Boundaries

The Python adapter detects HTTP routes from FastAPI/Starlette decorators and
emits `BOUNDARY` nodes so a Python service can be linked to consumers in other
languages. See [Cross-language linking](../guides/cross-language.md).

## CLI

```bash
graphlens analyze ./my-project --lang python --output graph.json
graphlens visualize ./my-project --lang python
```
