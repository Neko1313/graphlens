---
name: adapter-generator
description: Scaffolds a complete graphlens language adapter package from scratch. Use when asked to create an adapter, add language support, scaffold an adapter, or implement a new language adapter for graphlens. Produces all 5 source modules, pyproject.toml, and test stubs.
compatibility: Requires Python 3.13+, tree-sitter>=0.24, a tree-sitter-<lang> grammar package, uv workspace
allowed-tools: Bash Read Write Edit WebSearch
---

# graphlens Adapter Generator

Generates a production-ready `graphlens-<lang>` adapter package following the exact architecture of `graphlens-python`.

## Quick Start

1. **WebSearch** the target language (Step 0) — extensions, package managers, stdlib names
2. Read [Architecture Reference](references/ARCHITECTURE.md) — package layout, file roles
3. Read [Contracts Reference](references/CONTRACTS.md) — core ABCs, models, utilities
4. Read [Patterns Reference](references/PATTERNS.md) — code templates for every module
5. Read [Infrastructure Reference](references/INFRASTRUCTURE.md) — ruff, Taskfile, CI, codecov
6. Use [assets/adapter_template.md](assets/adapter_template.md) and [assets/visitor_template.md](assets/visitor_template.md) as starting skeletons
7. Generate files **bottom-up**: `_project_detector` → `_module_resolver` → `_deps` → `_visitor` → `_adapter` → `__init__` → `pyproject.toml` → linting → Taskfile → CI → codecov → tests

## Core Principles

- **Tree-sitter only** — every adapter must use tree-sitter as its parser (no stdlib `ast`, no regex)
- **Pure data producers** — adapters return a `GraphLens`; they never write to files, databases, or any backend
- **Entry points** — adapters register via `importlib.metadata` entry points; callers use `adapter_registry.load()`
- **Deterministic node IDs** — always use `make_node_id(project_name, qualified_name, kind.value)` (SHA-256[:16])
- **1-based spans** — tree-sitter positions are 0-based; always add +1 to row and col when constructing `Span`
- **ImportClassifier pre-pass** — build `ImportClassifier(stdlib, third_party, internal)` before visiting any file; every IMPORT node must have `metadata["origin"]` set
- **Three stacks** — visitor maintains `_scope_stack`, `_container_stack`, `_kind_stack` for scope tracking
- **`dep_parsers` constructor param** — adapters accept a custom parser list so callers can inject non-standard package managers

---

## Step-by-Step Generation Process

### Step 0 — Research the language (WebSearch)

Before collecting anything from the user, perform web searches to build accurate language knowledge:

1. **File extensions** — search `"{language} source file extensions"` and `"tree-sitter-{lang} grammar"`. Identify all commonly used extensions (e.g. `.ts`, `.tsx`, `.d.ts` for TypeScript). Include declaration/header files if they contain importable symbols.

2. **Package managers** — search `"{language} package managers"` and `"{language} dependency manifest files"`. Collect:
   - All mainstream package managers (e.g. npm, yarn, pnpm for Node; cargo for Rust; go mod for Go)
   - The manifest file name(s) each one uses
   - Where declared dependencies live inside each manifest (key paths)
   - Whether dev/test groups are separate keys

3. **Module system** — search `"{language} import system"` and `"{language} module resolution"`. Understand:
   - How file paths map to importable names
   - Relative vs absolute import syntax
   - How the language's equivalent of `__init__.py` / `index.ts` works

4. **Standard library / built-ins** — search `"{language} standard library modules list"`. Collect the top-level names callers import from the stdlib.

Document findings before proceeding to Step 1. This research drives `file_extensions()`, `{LANG}_MARKERS`, `DependencyFileParser` implementations, and `get_stdlib_names()`.

### Step 1 — Collect inputs from user

Required:
- **Language name** (e.g. `typescript`, `rust`, `go`) — used for `{lang}` placeholder
- **tree-sitter grammar package** (e.g. `tree-sitter-typescript`) — PyPI package name
- **File extensions** (e.g. `{".ts", ".tsx"}`)
- **Project marker files** (e.g. `package.json`, `tsconfig.json`)
- **Dependency manifest files** (e.g. `package.json`, `yarn.lock`) — drives `DependencyFileParser` implementations
- **Module path separator** — how the language maps file paths to module names

Optional (infer if not given):
- Equivalent of Python's `__init__` (package index file, e.g. `index.ts`)
- Relative import syntax
- Stdlib / built-in module names (or how to derive them)

### Step 2 — Scaffold directory structure

```
packages/graphlens-{lang}/
├── src/
│   └── graphlens_{lang}/
│       ├── __init__.py
│       ├── _adapter.py
│       ├── _visitor.py
│       ├── _deps.py
│       ├── _project_detector.py
│       └── _module_resolver.py
├── tests/
│   ├── conftest.py
│   ├── test_{lang}_adapter.py
│   ├── test_{lang}_visitor.py
│   ├── test_{lang}_deps.py
│   ├── test_{lang}_module_resolver.py
│   └── test_{lang}_project_detector.py
└── pyproject.toml
```

### Step 3 — Inspect the tree-sitter grammar

Before writing `_visitor.py`, run this to see what node types the grammar produces:

```python
import tree_sitter_{lang} as ts_lang
from tree_sitter import Language, Parser

lang = Language(ts_lang.language())
parser = Parser(lang)
tree = parser.parse(b"<minimal source snippet>")

def dump(node, indent=0):
    print(" " * indent + f"{node.type!r} [{node.start_point}–{node.end_point}]")
    for child in node.children:
        dump(child, indent + 2)

dump(tree.root_node)
```

Key node types to identify:
- Top-level program/source_file node
- Class / interface / struct declaration
- Function / method declaration
- Import statement
- Parameter nodes
- Call expression nodes

Document the node types before writing `_visit_*` handlers.

### Step 4 — Generate `_project_detector.py`

See [Patterns → _project_detector.py](references/PATTERNS.md#_project_detectorpy).

Key functions:
- `{LANG}_MARKERS: tuple[str, ...]` — marker file names
- `is_{lang}_project(root)` — checks markers or fallback (any source file)
- `find_{lang}_roots(search_root)` — monorepo support; returns list of sub-project roots
- `detect_project_name(project_root)` — reads manifest or falls back to dir name

### Step 5 — Generate `_module_resolver.py`

See [Patterns → _module_resolver.py](references/PATTERNS.md#_module_resolverpy).

Key functions:
- `find_source_roots(project_root, files)` — detect `src/` or similar layout
- `file_to_qualified_name(file_path, source_root)` — convert path to dotted module name
- Language-specific: handle index files (equivalent of `__init__.py`), extensions to strip

### Step 6 — Generate `_deps.py`

See [Patterns → _deps.py](references/PATTERNS.md#_depspy).

- One `DependencyFileParser` subclass per manifest format
- `get_stdlib_names() -> frozenset[str]` — language built-ins
- `{LANG}_DEFAULT_DEP_PARSERS` list
- All `parse()` implementations must return `frozenset()` on any error, never raise

### Step 7 — Generate `_visitor.py`

See [assets/visitor_template.md](assets/visitor_template.md) and [Patterns → _visitor.py](references/PATTERNS.md#_visitorpy).

Critical checklist:
- [ ] Module-level parser singleton (`_LANGUAGE`, `_parser`, `parse_{lang}()`)
- [ ] `ImportClassifier` dataclass with `classify(top_level)` method
- [ ] `VisitorContext` dataclass (project_name, file_path, source_root, module_qualified_name)
- [ ] `{Lang}ASTVisitor` with `visit()` dispatch via `getattr(self, f"_visit_{node.type}", None)`
- [ ] `_visit_children()` for default traversal
- [ ] Three stacks initialized in `__init__`: `_scope_stack`, `_container_stack`, `_kind_stack`
- [ ] Handlers for class/struct, function/method, import nodes
- [ ] `_emit_import()` — classifies origin, creates IMPORT node, emits IMPORTS + RESOLVES_TO relations
- [ ] `_get_or_create_external_symbol()` — idempotent EXTERNAL_SYMBOL creation
- [ ] `_make_span(ts_node)` — 0-based tree-sitter → 1-based Span
- [ ] Every IMPORT node has `metadata["origin"]` set

### Step 8 — Generate `_adapter.py`

See [assets/adapter_template.md](assets/adapter_template.md) and [Patterns → _adapter.py](references/PATTERNS.md#_adapterpy).

`_analyze_root()` pipeline order (must not deviate):
1. `detect_project_name()`
2. `find_source_roots()`
3. Pre-pass: collect `internal_tops` from file paths (no source parsing)
4. Run `DependencyFileParser` instances → `third_party` set
5. Build `ImportClassifier`
6. Create PROJECT node (guard duplicate with `if project_id not in graph.nodes`)
7. Per-file loop: `_ensure_module_chain()` → FILE node → parse → `{Lang}ASTVisitor`
8. Link PROJECT → top-level modules via CONTAINS

### Step 9 — Generate `__init__.py`

```python
"""graphlens_{lang} — {Language} language adapter for graphlens."""

from graphlens_{lang}._adapter import {Lang}Adapter

__all__ = ["{Lang}Adapter"]
```

### Step 10 — Generate `pyproject.toml`

See [Patterns → pyproject.toml](references/PATTERNS.md#pyprojecttoml).

Required: entry point `[project.entry-points."graphlens.adapters"]` → `{lang} = "graphlens_{lang}:{Lang}Adapter"`.

### Step 11 — Generate `ruff.toml`

See [Infrastructure → ruff.toml](references/INFRASTRUCTURE.md#rufftom).

Create `packages/graphlens-{lang}/ruff.toml`. The key rule: `[lint.per-file-ignores]` must relax annotation, docstring, and security rules for `tests/**` so pytest code doesn't require full production-grade typing.

### Step 12 — Generate `Taskfile.yaml`

See [Infrastructure → Taskfile.yaml](references/INFRASTRUCTURE.md#taskfileyaml).

Create `packages/graphlens-{lang}/Taskfile.yaml` with two tasks:
- `lint` — runs ruff + bandit + ty (plain mode for dev; JSON reports in CI mode)
- `test` — runs pytest with coverage (plain mode for dev; XML reports + JUnit in CI mode)

Then **update `taskfile.dist.yaml`** (workspace root) to include the new adapter:
```yaml
includes:
  {lang}:
    taskfile: packages/graphlens-{lang}/Taskfile.yaml
```
And add `{lang}:lint` / `{lang}:test` to the top-level `lint:` and `tests:` dependency lists.

Also add `release:bump` and `release:commit` steps to include the new `pyproject.toml`.

### Step 13 — Generate GitHub CI workflow

See [Infrastructure → GitHub CI](references/INFRASTRUCTURE.md#github-ci-workflow).

Create `.github/workflows/ci-{lang}.yml`. Follow the exact structure of `ci-python.yml`:
- Two jobs: `lint` and `test`
- `on.pull_request.paths` triggers on `packages/graphlens-{lang}/**`
- `task {lang}:lint CI=true` / `task {lang}:test CI=true`
- Codecov upload for `coverage.xml` with `flags: {lang}`
- Codecov test analytics upload for `junit.xml` with `flags: {lang}`
- Artifact upload for `packages/graphlens-{lang}/reports/`

### Step 14 — Update `codecov.yml`

See [Infrastructure → codecov.yml](references/INFRASTRUCTURE.md#codecovyml).

Add a new entry under `flag_management.individual_flags`:
```yaml
- name: {lang}
  paths:
    - packages/graphlens-{lang}/src/
```

### Step 15 — Scaffold tests

Mirror `packages/graphlens-python/tests/` structure. See [Patterns → Tests](references/PATTERNS.md#tests).

- `conftest.py` — shared fixtures (tmp_path helpers, minimal source fixtures)
- `test_{lang}_project_detector.py` — `is_{lang}_project`, `find_{lang}_roots`, `detect_project_name`
- `test_{lang}_module_resolver.py` — `file_to_qualified_name`, `find_source_roots`
- `test_{lang}_deps.py` — each parser's `can_parse` and `parse`, `get_stdlib_names`
- `test_{lang}_visitor.py` — visitor unit tests per node type
- `test_{lang}_adapter.py` — end-to-end: real source snippets → correct graph structure

---

## Placeholder conventions

| Placeholder | Meaning | Example |
|---|---|---|
| `{lang}` | snake_case language name | `typescript` |
| `{Lang}` | PascalCase | `Typescript` |
| `{LANG}` | UPPER_CASE | `TYPESCRIPT` |
| `{language}` | human-readable full name | `TypeScript` |
| `{ext}` | primary file extension | `.ts` |
| `{marker}` | primary project marker file | `package.json` |
