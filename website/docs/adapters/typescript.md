---
sidebar_position: 3
---

# TypeScript adapter

The TypeScript adapter parses `.ts`/`.tsx` (and JavaScript) with Tree-sitter and
resolves types through the **TypeScript Compiler API**, driven from a Node
subprocess.

## Install

```bash
pip install "graphlens[typescript]"
# or, with the CLI
pip install "graphlens-cli[typescript]"
```

The resolver needs **Node** available on the `PATH`. TypeScript itself is
installed on demand into a cache directory, so you do not have to add it to your
project.

## Use

```python
from pathlib import Path
from graphlens import adapter_registry

adapter = adapter_registry.load("typescript")()
graph = adapter.analyze(Path("./my-frontend"))
```

The package exports `TypescriptAdapter` and its resolver `TsResolver`:

```python
from graphlens_typescript import TypescriptAdapter, TsResolver
```

| Property | Value |
|---|---|
| Language id | `typescript` |
| Project marker | `package.json` |
| Resolver | `TsResolver` |
| Engine | TypeScript Compiler API (Node subprocess) |

## How resolution works

`TsResolver` is a Node-subprocess resolver. Rather than querying one position at
a time, it **batches all occurrence queries into a single `resolve_all` call** to
a bundled `ts_resolver.js` script, which loads the project with the Compiler API
and answers them together. TypeScript is installed on demand into a cache dir
the first time it runs. The resolved definitions become `CALLS` / `REFERENCES`
/ `HAS_TYPE` / `INHERITS_FROM` edges just as with every other language.

As always, check the
[resolver status](../getting-started/concepts.md#resolver-status) (or run with
`--strict`) before trusting the type-aware edges; without Node the adapter
falls back to a structure-only graph.

## Dependency classification

Third-party packages are read from `package.json` (including `devDependencies`),
internal modules from the source layout, and the rest classified as `unknown`.
Scoped npm names (`@scope/pkg`) are preserved. Every `IMPORT` node carries an
`origin`.

## Boundaries

The TypeScript adapter detects HTTP clients (for example `fetch` calls) and
emits `CONSUMES` edges to `BOUNDARY` nodes, which lets a TypeScript front end be
linked to a backend route exposed by another language. See
[Cross-language linking](../guides/cross-language.md).

## CLI

```bash
graphlens analyze ./my-frontend --lang typescript --output graph.json
graphlens analyze ./monorepo --lang python,typescript
```
