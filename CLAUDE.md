# code-graph — Architecture Guide

## Project layout

```
code-graph/                      ← uv workspace root (also the core library)
  src/code_graph/                ← core: models, contracts, registry, exceptions, utils
  packages/
    code-graph-python/           ← Python language adapter
  tests/                         ← core library tests
  examples/                      ← runnable usage examples
```

All packages use the `src/` layout and `uv_build` as build backend.

Each language adapter follows this internal layout:
```
packages/code-graph-<lang>/
  src/code_graph_<lang>/
    __init__.py              ← exports <Lang>Adapter
    _adapter.py              ← LanguageAdapter subclass + _analyze_root()
    _visitor.py              ← ASTVisitor + ImportClassifier
    _deps.py                 ← DependencyFileParser implementations + default list
    _project_detector.py     ← is_<lang>_project(), find_<lang>_roots(), detect_project_name()
    _module_resolver.py      ← file→qualified_name, source root detection
```

---

## Core principles

### 1. Adapters are pure data producers
An adapter parses source files and returns a `CodeGraph`. It never writes to
any backend, database, or file system. The graph is the only output.

### 2. code-graph core is minimal
`core` lives at the workspace root under `src/code_graph/`. It contains only:
models, contracts (ABCs), registry, exceptions, utils.
No pipeline, no orchestration, no I/O. Orchestration belongs in a separate
package or in user code.

### 3. SQLAlchemy dialect pattern for adapters
Adapters register themselves via `importlib.metadata` entry points:

```toml
# In the adapter's pyproject.toml
[project.entry-points."code_graph.adapters"]
python = "code_graph_python:PythonAdapter"
```

Callers resolve adapters through the registry — no direct imports needed:

```python
from code_graph import adapter_registry
adapter = adapter_registry.load("python")()
graph = adapter.analyze(project_root)
```

### 4. Tree-sitter for all language adapters
Every adapter must use Tree-sitter as its parser. This gives:
- A single unified traversal pattern across all languages
- Error-tolerant parsing (`root_node.has_error` instead of exceptions)
- CST with exact byte positions for `Span`

Parser setup (one module-level singleton per adapter):
```python
import tree_sitter_<lang> as ts_lang
from tree_sitter import Language, Parser, Node as TSNode

_LANGUAGE = Language(ts_lang.language())
_parser = Parser(_LANGUAGE)

def parse_<lang>(source: bytes) -> tree_sitter.Tree:
    return _parser.parse(source)
```

### 5. Visitor pattern: dispatch by node.type
```python
class <Lang>ASTVisitor:
    def visit(self, node: TSNode) -> None:
        handler = getattr(self, f"_visit_{node.type}", None)
        if handler:
            handler(node)
        else:
            self._visit_children(node)

    def _visit_children(self, node: TSNode) -> None:
        for child in node.children:
            self.visit(child)
```

All state lives on three stacks pushed/popped as scope changes:
- `_scope_stack: list[str]` — qualified name prefix
- `_container_stack: list[str]` — current parent node ID
- `_kind_stack: list[NodeKind]` — to distinguish METHOD vs FUNCTION

The visitor receives an `ImportClassifier` (see §9) and must set
`metadata["origin"]` on every `IMPORT` node.

### 6. Deterministic node IDs
```python
# src/code_graph/utils/ids.py
def make_node_id(project_name: str, qualified_name: str, kind: str) -> str:
    key = f"{project_name}::{kind}::{qualified_name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

Same inputs → same ID across re-scans. Enables incremental updates.

### 7. Multi-language / monorepo support
`LanguageAdapter.can_handle(root)` must return `True` for multi-language
projects even when language-specific marker files live in sub-directories.

`analyze(root)` without explicit `files` must:
1. Call `find_<lang>_roots(root)` to locate actual project sub-roots
2. Detect project name and source roots **relative to each sub-root**
3. Create one `PROJECT` node per sub-root in the shared `CodeGraph`

This ensures module qualified names and import mappings are correct when
`root` is a monorepo containing multiple independent projects.

### 8. Spans are 1-based
Tree-sitter positions are 0-based `(row, col)`. Convert to 1-based when
constructing `Span(start_line, start_col, end_line, end_col)`:
```python
Span(
    start_line=node.start_point[0] + 1,
    start_col=node.start_point[1] + 1,
    end_line=node.end_point[0] + 1,
    end_col=node.end_point[1] + 1,
)
```

### 9. Import origin classification

Every `IMPORT` node must have `metadata["origin"]` set to one of:

| Value | Meaning |
|-------|---------|
| `"stdlib"` | Language standard library (os, sys, path, …) |
| `"internal"` | Module declared within the same project |
| `"third_party"` | Package listed in a dependency manifest |
| `"unknown"` | None of the above (transitive dep, missing, etc.) |

Classification pipeline in `_analyze_root()`, before visiting any file:

1. **Internal** — pre-pass: derive top-level module names from file paths via
   the module resolver (no source parsing needed).
2. **Third-party** — run all `DependencyFileParser` instances that `can_parse()`
   the project root, union results.
3. **Stdlib** — language built-ins (e.g. `sys.stdlib_module_names` for Python).

Build an `ImportClassifier(stdlib, third_party, internal)` and pass it to the
visitor. Relative imports are always `"internal"` regardless of the classifier.

For `"internal"` imports: resolve `RESOLVES_TO` to the existing `MODULE` node
when it is present in the graph; fall back to `EXTERNAL_SYMBOL` so the edge
is never missing when the target file hasn't been processed yet.

`DependencyFileParser` rules:
- One parser per file format — compose via a `<LANG>_DEFAULT_DEP_PARSERS` list.
- Different package managers (poetry vs pip-tools; pnpm vs yarn) get separate
  parsers or configurable key-paths within one parser.
- Include dev/test groups so test imports classify as `third_party`.
- Return `frozenset()` on any error — never raise.
- Use `normalize_pkg_name()` from `code_graph` for consistent comparison:
  lowercase, hyphens→underscores, strip extras/version specifiers.

Adapters expose `dep_parsers` as a constructor parameter so callers can inject
custom parsers for non-standard setups without subclassing the adapter.

### 10. File collection belongs to the adapter
`LanguageAdapter` provides `file_extensions()` and a default `collect_files()`
implementation. Callers must not build file lists manually. Pass explicit
`files=` only for incremental updates or custom filtering.

---

## Graph model

```
NodeKind:     PROJECT  MODULE  FILE  CLASS  METHOD  FUNCTION
              PARAMETER  IMPORT  SYMBOL  EXTERNAL_SYMBOL  DEPENDENCY

RelationKind: CONTAINS  DECLARES  IMPORTS  RESOLVES_TO
              CALLS  REFERENCES  INHERITS_FROM  DEPENDS_ON
```

Typical hierarchy built by an adapter:

```
PROJECT
  └─(CONTAINS)─ MODULE (top-level)
                  └─(CONTAINS)─ MODULE (nested)
                                  └─(CONTAINS)─ FILE
                                                  └─(DECLARES)─ CLASS
                                                                  └─(DECLARES)─ METHOD
                                                  └─(DECLARES)─ FUNCTION
                                                  └─(DECLARES)─ IMPORT ─(RESOLVES_TO)─ MODULE (internal)
                                                                        └─(RESOLVES_TO)─ EXTERNAL_SYMBOL (stdlib/third_party/unknown)
FUNCTION/METHOD ─(CALLS)─ SYMBOL
CLASS ─(INHERITS_FROM)─ EXTERNAL_SYMBOL
```

EXTERNAL_SYMBOL always carries `metadata["origin"]` = `"stdlib"` | `"third_party"` |
`"internal"` (fallback when MODULE node not yet in graph) | `"unknown"`.

---

## Adding a new language adapter

Key checklist:
1. `packages/code-graph-<lang>/` with `src/` layout
2. `tree-sitter>=0.24` + `tree-sitter-<lang>` in dependencies
3. Entry point `"code_graph.adapters"` → `"<lang>"`
4. `LanguageAdapter` subclass: `language()`, `can_handle()`, `file_extensions()`, `analyze()`
5. `find_<lang>_roots()` for monorepo support
6. `_deps.py`: `DependencyFileParser` implementations + `<LANG>_DEFAULT_DEP_PARSERS`
7. `ImportClassifier` pre-pass in `_analyze_root()`, `origin` on every IMPORT node
8. Adapter accepts `dep_parsers` constructor param for custom override
9. Visitor: dispatch by `node.type`, three stacks, `make_node_id` for IDs
10. Tests mirror `packages/code-graph-python/tests/` structure including `test_<lang>_deps.py`

## Adding a dependency parser for an existing adapter

Add a new `DependencyFileParser` subclass in the adapter's `_deps.py`, append
it to the `<LANG>_DEFAULT_DEP_PARSERS` list. Return `frozenset()` on any error.
