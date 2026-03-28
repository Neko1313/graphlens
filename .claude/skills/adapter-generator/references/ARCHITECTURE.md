# Architecture Reference

## Package layout

Every language adapter follows the `src/` layout and lives in the `packages/` directory of the workspace:

```
packages/graphlens-{lang}/
├── src/
│   └── graphlens_{lang}/
│       ├── __init__.py              ← exports only {Lang}Adapter
│       ├── _adapter.py              ← LanguageAdapter subclass + _analyze_root()
│       ├── _visitor.py              ← tree-sitter parser setup, ImportClassifier,
│       │                               VisitorContext, {Lang}ASTVisitor
│       ├── _deps.py                 ← DependencyFileParser implementations +
│       │                               get_stdlib_names() + {LANG}_DEFAULT_DEP_PARSERS
│       ├── _project_detector.py     ← is_{lang}_project(), find_{lang}_roots(),
│       │                               detect_project_name()
│       └── _module_resolver.py      ← file_to_qualified_name(), find_source_roots(),
│                                       resolve_relative_import() (if applicable)
├── tests/
│   ├── conftest.py
│   ├── test_{lang}_adapter.py
│   ├── test_{lang}_visitor.py
│   ├── test_{lang}_deps.py
│   ├── test_{lang}_module_resolver.py
│   └── test_{lang}_project_detector.py
└── pyproject.toml
```

## File responsibilities

| File | Responsibility |
|---|---|
| `__init__.py` | Single public export — `{Lang}Adapter` only |
| `_adapter.py` | Orchestrates the full analysis pipeline; ties together all subsystems |
| `_visitor.py` | Walks tree-sitter CST; emits Node and Relation objects into GraphLens |
| `_deps.py` | Reads manifest files to build the third-party package name set |
| `_project_detector.py` | Detects language roots and reads project name from manifests |
| `_module_resolver.py` | Converts file paths to module qualified names; resolves relative imports |

## Module naming

- Package directory: `graphlens-{lang}` (hyphenated)
- Python import name: `graphlens_{lang}` (underscored)
- Adapter class: `{Lang}Adapter`

## Entry point registration

```toml
# packages/graphlens-{lang}/pyproject.toml
[project.entry-points."graphlens.adapters"]
{lang} = "graphlens_{lang}:{Lang}Adapter"
```

This allows callers to load the adapter without a direct import:

```python
from graphlens import adapter_registry
adapter = adapter_registry.load("{lang}")()
graph = adapter.analyze(project_root)
```

## Workspace integration

The workspace root `pyproject.toml` must include the new package:

```toml
# graphlens/pyproject.toml  (workspace root)
[tool.uv.workspace]
members = [
    "packages/graphlens-python",
    "packages/graphlens-{lang}",   # ← add this
]
```

The adapter's own `pyproject.toml` declares `graphlens` as a workspace dependency:

```toml
[tool.uv.sources]
graphlens = { workspace = true }
```

## Invariants

- Adapters are **pure data producers** — `analyze()` returns a `GraphLens` and does nothing else
- Every source file must be parseable via tree-sitter; partial results are acceptable on `root_node.has_error`
- Module qualified names derived from file paths must be deterministic and stable across re-scans
- All node IDs are produced by `make_node_id()` — never construct IDs manually
- `DependencyFileParser.parse()` must return `frozenset()` on any error, never raise
