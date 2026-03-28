<div align="center">

  <h1>code-graph</h1>

  <p>Extensible polyglot code analysis framework that parses source projects, normalizes their structure into a shared graph IR, and exposes it for dependency analysis, navigation, and code intelligence tooling.</p>

  [![PyPI](https://img.shields.io/pypi/v/code-graph?color=blue)](https://pypi.org/project/code-graph/)
  [![Python](https://img.shields.io/pypi/pyversions/code-graph)](https://pypi.org/project/code-graph/)
  [![License](https://img.shields.io/github/license/Neko1313/code-graph)](LICENSE)
  [![CI](https://img.shields.io/github/actions/workflow/status/Neko1313/code-graph/ci.yml?label=CI)](https://github.com/Neko1313/code-graph/actions)
  [![codecov](https://codecov.io/gh/Neko1313/code-graph/graph/badge.svg)](https://codecov.io/gh/Neko1313/code-graph)

  [Repository](https://github.com/Neko1313/code-graph) · [Issues](https://github.com/Neko1313/code-graph/issues)

</div>

---

## Architecture

```
Repository → Language Adapter → CodeGraph (IR) → Graph Backend
```

| Layer | Responsibility |
|---|---|
| **Language Adapter** | Parses source files, produces `CodeGraph` |
| **CodeGraph** | Typed nodes + directed relations (the IR) |
| **Graph Backend** | Persists or queries the graph (Neo4j, in-memory, …) |

Adapters are **pure data producers** — they never write to any backend. The graph is the only output.

## Why graph IR?

- **Language-agnostic** — one shared model for Python, TypeScript, Rust, …
- **Plugin-based adapters** — each language is a separate package, registered via Python entry points
- **Tree-sitter powered** — all adapters use tree-sitter for error-tolerant CST parsing and exact span positions
- **Monorepo aware** — `can_handle()` and `find_*_roots()` handle multi-language repos correctly
- **Deterministic node IDs** — SHA-256 hash of `project::kind::qualified_name` → stable across re-scans

## Installation

```bash
# Core library only (models, contracts, registry)
pip install code-graph

# Core + Python adapter
pip install "code-graph[python]"
```

With uv:

```bash
uv add code-graph
uv add "code-graph[python]"
```

## Quick start

```python
from pathlib import Path
from code_graph import adapter_registry

# Load and instantiate the Python adapter
adapter = adapter_registry.load("python")()

# Analyze a project — returns a CodeGraph
graph = adapter.analyze(Path("./my-project"))

print(f"Nodes:     {len(graph.nodes)}")
print(f"Relations: {len(graph.relations)}")

# Inspect nodes by kind
from code_graph import NodeKind

modules = [n for n in graph.nodes.values() if n.kind == NodeKind.MODULE]
classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
```

## Graph model

### Node kinds

| Kind | Description |
|---|---|
| `PROJECT` | Root project node |
| `MODULE` | Python/TS/… module (directory or file) |
| `FILE` | Source file |
| `CLASS` | Class declaration |
| `FUNCTION` | Top-level function |
| `METHOD` | Method inside a class |
| `PARAMETER` | Function/method parameter |
| `IMPORT` | Import statement |
| `DEPENDENCY` | Declared package dependency |
| `SYMBOL` | Internal symbol reference |
| `EXTERNAL_SYMBOL` | External symbol (stdlib, third-party, unknown) |

### Relation kinds

| Kind | Description |
|---|---|
| `CONTAINS` | Structural containment (project → module → file → class) |
| `DECLARES` | Declaration (file declares function, class declares method) |
| `IMPORTS` | Import edge (file → import node) |
| `RESOLVES_TO` | Import resolved to a module or external symbol |
| `CALLS` | Function/method call |
| `REFERENCES` | Symbol reference |
| `INHERITS_FROM` | Class inheritance |
| `DEPENDS_ON` | Package dependency |

## Adapter plugin system

Language adapters register themselves via Python entry points — no changes to the core needed:

```toml
# packages/code-graph-python/pyproject.toml
[project.entry-points."code_graph.adapters"]
python = "code_graph_python:PythonAdapter"
```

The registry discovers installed adapters automatically at runtime:

```python
from code_graph import adapter_registry

adapter_registry.available()          # ["python", ...]
adapter_cls = adapter_registry.load("python")
adapter = adapter_cls()
```

Adapters can also be registered manually (useful for testing):

```python
adapter_registry.register("python", MyPythonAdapter)
```

## Implementing an adapter

Subclass `LanguageAdapter` and implement four methods:

```python
from pathlib import Path
from code_graph import CodeGraph, LanguageAdapter

class MyLangAdapter(LanguageAdapter):
    def language(self) -> str:
        return "mylang"

    def file_extensions(self) -> set[str]:
        return {".ml", ".mli"}

    def can_handle(self, project_root: Path) -> bool:
        return (project_root / "dune-project").exists()

    def analyze(
        self, project_root: Path, files: list[Path] | None = None
    ) -> CodeGraph:
        graph = CodeGraph()
        files = files or self.collect_files(project_root)
        # ... parse and populate graph ...
        return graph
```

Register in `pyproject.toml` and the core registry finds it automatically.

## Project structure

```
code-graph/                      ← uv workspace root (core library)
  src/code_graph/                ← models, contracts, registry, exceptions, utils
  packages/
    code-graph-python/           ← Python adapter (tree-sitter)
  tests/                         ← core tests (100% coverage)
  examples/                      ← runnable usage examples
```

## Development

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), [task](https://taskfile.dev/).

```bash
task install        # uv sync --all-groups
task lint           # ruff + ty + bandit for all packages
task tests          # all tests with coverage
```

Individual package tasks:

```bash
task core:lint      task core:test
task python:lint    task python:test
```

## License

MIT
