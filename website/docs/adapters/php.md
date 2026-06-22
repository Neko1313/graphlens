---
sidebar_position: 6
---

# PHP adapter

The PHP adapter parses `.php` / `.phtml` / `.inc` files with Tree-sitter and
resolves symbols through [`phpactor`](https://phpactor.readthedocs.io/), an
open-source PHP language server driven over stdio. The default
`PhpactorResolver` spawns one `phpactor language-server` subprocess per scan
and answers cross-file `definition_at` queries with `textDocument/definition`,
emitting `CALLS` / `REFERENCES` / `HAS_TYPE` / `INHERITS_FROM` edges.

Structure (namespaces, classes, interfaces, traits, enums, methods,
properties, constants, `use` imports) is always produced from Tree-sitter
alone — the resolver only adds the type-aware edges on top, and degrades
honestly via the [resolver status](../getting-started/concepts.md#resolver-status)
when it is unavailable.

:::info Get it through Docker
The PHP adapter is **not published to PyPI**. The supported way to use it is the
[Docker image](../ci-integration/docker.md), which bundles the adapter together
with the PHP runtime, Composer, and `phpactor`:

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

The package exports `PhpAdapter` and its resolvers:

```python
from graphlens_php import PhpAdapter, PhpactorResolver, PhpResolver
```

| Property | Value |
|---|---|
| Language id | `php` |
| Project marker | `composer.json` |
| Resolver | `PhpactorResolver` (default) |
| Engine | `phpactor language-server` (LSP, stdio) |

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

### Resolvers

| Resolver | Engine | Use it for |
|---|---|---|
| `PhpactorResolver` (default) | `phpactor language-server` (LSP) | Cross-file resolution of calls, references, type uses, and base classes. |
| `PhpResolver` | none (structure only) | Explicitly skip type-aware resolution; always reports `unavailable`. |

Inject a non-default resolver through the constructor:

```python
from graphlens_php import PhpAdapter, PhpResolver

adapter = PhpAdapter(resolver=PhpResolver())
```

## Requirements

`PhpactorResolver` drives `phpactor`, which runs on PHP — both must be on the
`PATH` (point `phpactor` somewhere else with `$GRAPHLENS_PHPACTOR`). Both are
pre-installed in the Docker image, along with Composer so a project's `vendor/`
tree can be populated for precise third-party resolution. If `phpactor` cannot
start, the adapter falls back to a structure-only graph and reports a non-`ok`
resolver status.

## CLI

```bash
docker run --rm -v "$PWD:/workspace" ghcr.io/neko1313/graphlens \
    analyze /workspace --lang php
```
