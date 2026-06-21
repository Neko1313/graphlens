---
sidebar_position: 6
---

# Writing an adapter

Adding a language means writing a new adapter package. The core stays untouched —
your package registers itself through an entry point and the registry finds it.
This page is the practical checklist; the
[contracts API reference](../api-reference/contracts.md) has the formal
signatures.

## The minimal contract

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
        self, project_root: Path, files: list[Path] | None = None, *, strict: bool = False
    ) -> GraphLens:
        graph = GraphLens()
        files = files or self.collect_files(project_root)
        # ... parse and populate graph ...
        return graph
```

`collect_files` has a default implementation driven by `file_extensions()`, so
callers never build file lists by hand.

## Register the adapter

Add the entry point to your package's `pyproject.toml`:

```toml
[project.entry-points."graphlens.adapters"]
mylang = "graphlens_mylang:MyLangAdapter"
```

Once installed, `adapter_registry.load("mylang")` resolves it automatically.

## Package layout

Each adapter follows the same internal structure:

```
packages/graphlens-mylang/
  src/graphlens_mylang/
    __init__.py              # exports MyLangAdapter (+ resolver if public)
    _adapter.py              # LanguageAdapter subclass + _analyze_root()
    _visitor.py              # ASTVisitor + ImportClassifier + OccurrenceRef
    _resolver.py             # SymbolResolver subclass
    _deps.py                 # DependencyFileParser implementations + default list
    _project_detector.py     # is_<lang>_project(), find_<lang>_roots(), detect_project_name()
    _module_resolver.py      # file → qualified_name, source root detection
```

## The analysis pipeline

A well-behaved adapter runs this pipeline inside `_analyze_root()`:

### Before visiting any file (pre-pass)

1. **Internal modules** — derive top-level module names from file paths via the
   module resolver (no parsing needed).
2. **Third-party** — run every `DependencyFileParser` whose `can_parse()` is
   true for the root and union the results.
3. **Stdlib** — the language's built-in module set.

Build an `ImportClassifier(stdlib, third_party, internal)` and hand it to the
visitor. Every `IMPORT` node must end up with `metadata["origin"]` set to
`stdlib` / `internal` / `third_party` / `unknown` (relative imports are always
`internal`).

### Visiting (Tree-sitter)

Use a visitor that dispatches by `node.type`:

```python
class MyLangASTVisitor:
    def visit(self, node):
        handler = getattr(self, f"_visit_{node.type}", None)
        if handler:
            handler(node)
        else:
            self._visit_children(node)
```

Keep scope state on three stacks (qualified-name prefix, current parent id,
node kind), build deterministic IDs with `make_node_id`, and record
`metadata["name_span"]` on every structural node. The visitor **collects**
`OccurrenceRef`s for use-sites (each with a role: `call` / `read` / `write` /
`annotation` / `base`) — it does **not** emit `CALLS`/`REFERENCES`/`HAS_TYPE`/
`INHERITS_FROM` itself.

Remember Tree-sitter positions are 0-based `(row, col)`; convert to 1-based when
building a [`Span`](../api-reference/models.md#span).

### After visiting all files (resolution pass)

4. Build a `SpanIndex` from the completed graph — the location → node bridge.
5. Call `resolver.prepare(project_root, files)`.
6. For each occurrence, call `resolver.definition_at(file, line, col)`.
7. Look up the target with `SpanIndex.at(...)` and emit the appropriate edge
   (`CALLS` / `REFERENCES` / `HAS_TYPE` / `INHERITS_FROM`). If the target is not
   in the graph, fall back to an `EXTERNAL_SYMBOL` node.

Record the [resolver status](../getting-started/concepts.md#resolver-status) on
the graph metadata so `strict=True` and `--strict` work.

## The resolver

Implement `SymbolResolver` and **never raise** — every method returns `None` or
`[]` on failure, so a missing toolchain degrades gracefully instead of crashing
the analysis. See the [contracts reference](../api-reference/contracts.md#symbolresolver).

## Dependency parsers

One `DependencyFileParser` per file format, composed into a
`<LANG>_DEFAULT_DEP_PARSERS` list:

- different package managers get separate parsers (or configurable key-paths);
- include dev/test groups so test imports classify as `third_party`;
- return `frozenset()` on any error — never raise;
- normalize names with `normalize_pkg_name()` for consistent comparison.

Expose `dep_parsers` as a constructor parameter so callers can inject custom
parsers without subclassing.

## Monorepo support

Implement `find_<lang>_roots()` so analysis handles multi-language repos:

- locate every real project sub-root, not just `root` itself;
- if `root` is both a project and a monorepo, return `root` **and** every nested
  project root for the same language;
- while analyzing a parent root, exclude files that belong to nested roots so a
  child project is not also modeled as a module of the parent.

## Scaffold it automatically

The repository ships an `adapter-generator` skill that scaffolds the full
package — all five source modules, `pyproject.toml`, and test stubs — from
scratch. It is the fastest way to start a new adapter with the conventions
already in place.

## Tests

Mirror the structure of `packages/graphlens-python/tests/`, including a
`test_<lang>_deps.py` for the dependency parsers.
