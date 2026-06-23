---
sidebar_position: 6
---

# PHP adapter

The PHP adapter parses `.php` / `.phtml` / `.inc` files with Tree-sitter and
resolves symbols through [PHPantom](https://crates.io/crates/phpantom_lsp), an
open-source PHP language server written in Rust and driven over stdio. The
default `PhpantomResolver` spawns one `phpantom_lsp --stdio` subprocess per
scan and answers cross-file `definition_at` queries with
`textDocument/definition`, emitting `CALLS` / `REFERENCES` / `HAS_TYPE` /
`INHERITS_FROM` edges. The queries are pipelined — every occurrence is written
up front and responses are collected by id — so a whole project resolves at
thousands of definitions per second instead of one blocking round-trip each.

Structure (namespaces, classes, interfaces, traits, enums, methods,
properties, constants, `use` imports) is always produced from Tree-sitter
alone — the resolver only adds the type-aware edges on top, and degrades
honestly via the [resolver status](../getting-started/concepts.md#resolver-status)
when it is unavailable.

:::info Get it through Docker
The PHP adapter is **not published to PyPI**. The supported way to use it is the
[Docker image](../ci-integration/docker.md), which bundles the adapter together
with the `phpantom_lsp` binary (plus a minimal PHP runtime and Composer used
only to populate a project's `vendor/` tree):

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --lang php --output /workspace/graph.json
```
:::

## Use

```python
from pathlib import Path
from graphlens import adapter_registry

adapter = adapter_registry.load("php")()
graph = adapter.analyze(Path("./my-app"))
```

The package exports `PhpAdapter` and its resolver:

```python
from graphlens_php import PhpAdapter, PhpantomResolver
```

| Property | Value |
|---|---|
| Language id | `php` |
| Project marker | `composer.json` |
| Resolver | `PhpantomResolver` (default) |
| Engine | `phpantom_lsp --stdio` (LSP, stdio) |

### Namespaces & PSR-4

PHP has no module system; the adapter models **namespaces** as the `MODULE`
hierarchy. A file's namespace is taken from its in-source `namespace`
declaration (authoritative), falling back to the project's `composer.json`
`autoload` / `autoload-dev` **PSR-4** map. Files in the global namespace are
contained directly by the `PROJECT` node.

### Dependency classification

`use` imports are classified into `stdlib` / `internal` / `third_party` /
`unknown`:

- **internal** — the namespace's top segment is a PSR-4 prefix declared in
  `composer.json`.
- **third_party** — the lowercased top segment matches a Composer **vendor**
  (e.g. `Symfony\…` ↔ `symfony/console`, `Monolog\…` ↔ `monolog/monolog`).
  Composer package names are not namespaces, so this manifest-level match is a
  heuristic; the resolver corrects the rest from the real `vendor/` tree.
- **stdlib** — an unqualified `use` of a PHP built-in class (`DateTime`,
  `Exception`, `PDO`, …).
- **unknown** — anything else.

### Resolver

| Resolver | Engine | What it emits |
|---|---|---|
| `PhpantomResolver` (default) | `phpantom_lsp --stdio` (Rust LSP) | Fast cross-file resolution of calls, references, type uses, and base classes — no PHP runtime needed. |

`PhpantomResolver` is the only resolver. When the `phpantom_lsp` binary is
absent it degrades automatically — reporting `unavailable` and producing a
structure-only graph — so there is no separate "structure-only" resolver to
choose. Inject a custom `SymbolResolver` subclass through the constructor to
override it:

```python
from graphlens_php import PhpAdapter, PhpantomResolver

adapter = PhpAdapter(resolver=PhpantomResolver())
```

## Requirements

`PhpantomResolver` drives the `phpantom_lsp` Rust binary — a self-contained
executable that needs no PHP runtime. It must be on the `PATH` (point it
elsewhere with `$GRAPHLENS_PHPANTOM`; the resolver also accepts a `phpantom`
binary name). It is pre-installed in the Docker image, along with a minimal PHP
runtime and Composer so a project's `vendor/` tree can be populated for precise
third-party resolution. If the server cannot start, the adapter falls back to a
structure-only graph and reports a non-`ok` resolver status.

## CLI

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --lang php
```
