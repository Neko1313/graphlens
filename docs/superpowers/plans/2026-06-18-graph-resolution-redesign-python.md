# Graph Resolution Redesign (Python) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the graph model and Python adapter so calls/references/types resolve to the real declaration nodes (PyCharm-level connectivity) instead of name-level `SYMBOL` placeholders.

**Architecture:** tree-sitter keeps fast structure extraction and occurrence *roles* (call / read / write / annotation / base) + spans; `jedi` provides resolution (which declaration a name binds to, cross-file, with type inference). A new core `SpanIndex` maps an engine location (file + 1-based line/col) back to a graph node via its `name_span`; a new core `SymbolResolver` ABC marks the type-aware engine seam, implemented by `JediResolver`. The adapter runs occurrence-driven: tree-sitter collects occurrences with their enclosing node id; the resolver resolves each to a target node and emits `CALLS`/`REFERENCES`/`HAS_TYPE`/`INHERITS_FROM` edges.

**Tech Stack:** Python 3.13, tree-sitter + tree-sitter-python, jedi, pytest, uv workspace, ruff, ty.

## Global Constraints

- Python `>=3.13`; build backend `uv_build` (`>=0.9.18,<0.12.0`); `src/` layout.
- ruff: line-length `79`, target `py313`, full annotations + docstrings enforced (tests relaxed).
- ty type-check must pass.
- Coverage: core `fail_under=90`; Python adapter `fail_under=100`.
- New dependency floor: `jedi>=0.19.2` (Python adapter only).
- All `Span` values 1-based (line AND col). tree-sitter is 0-based → `+1`. jedi is 1-based line / 0-based col → convert at the `JediResolver` boundary only.
- All node IDs via `make_node_id(project_name, qualified_name, kind)` (deterministic).
- Dependency parsers and the resolver must never raise on bad input — return empty / `None`.
- Adapters never write to a backend; the `GraphLens` is the only output.
- Conventional-commit messages; commit after every task.

## Global Interfaces (locked — every task must match these names/types verbatim)

**Core model (Task 1, 5):**
- `NodeKind` adds: `VARIABLE = "variable"`, `ATTRIBUTE = "attribute"`, `TYPE_ALIAS = "type_alias"`. Removes `SYMBOL` (in Task 5, after the visitor stops using it).
- `RelationKind` adds: `HAS_TYPE = "has_type"`.

**`SpanIndex` — `src/graphlens/utils/span_index.py` (Task 2):**
```python
class SpanIndex:
    def __init__(self) -> None: ...
    def add_full(self, file_path: str, node_id: str, span: Span) -> None: ...
    def add_name(self, file_path: str, node_id: str, name_span: Span) -> None: ...
    @classmethod
    def from_graph(cls, graph: GraphLens) -> SpanIndex: ...
    def enclosing(self, file_path: str, line: int, col: int) -> str | None: ...
    def at(self, file_path: str, line: int, col: int) -> str | None: ...
```
- `file_path` keys are **absolute** path strings. Coordinates are **1-based** line and col.
- `from_graph` indexes every node that has a string `file_path`: by full `span` (`add_full`) and, when `metadata["name_span"]` is a `Span`, by name span (`add_name`).
- `enclosing` returns the node whose full span contains `(line, col)` with the **smallest** area (innermost). `at` returns the node whose **name span** contains `(line, col)` (smallest on ties). Both return `None` if nothing matches.

**`SymbolResolver` + DTOs — `src/graphlens/contracts/resolver.py` (Task 3):**
```python
@dataclass(frozen=True)
class ResolvedRef:
    full_name: str
    file_path: Path | None      # absolute; None for builtins/compiled
    line: int                   # 1-based
    col: int                    # 1-based
    kind: str                   # best-effort: function|class|method|module|variable|param|instance|unknown
    origin: str                 # stdlib|internal|third_party|unknown

@dataclass(frozen=True)
class Occurrence:
    file_path: Path             # absolute
    line: int                   # 1-based
    col: int                    # 1-based
    is_definition: bool
    access: str                 # read|write|call|unknown

class SymbolResolver(ABC):
    @abstractmethod
    def prepare(self, project_root: Path, files: list[Path]) -> None: ...
    @abstractmethod
    def definition_at(self, file: Path, line: int, col: int) -> ResolvedRef | None: ...
    @abstractmethod
    def infer_type_at(self, file: Path, line: int, col: int) -> ResolvedRef | None: ...
    @abstractmethod
    def references_to(self, file: Path, line: int, col: int) -> list[Occurrence]: ...
```
- All inputs are **1-based** line AND col (graphlens convention).

**`OccurrenceRef` — collected by the visitor, consumed by the adapter (Task 5, 6):**
```python
@dataclass(frozen=True)
class OccurrenceRef:
    role: str           # call|read|write|annotation|base
    line: int           # 1-based, position of the referenced name
    col: int            # 1-based
    enclosing_id: str   # node id of the enclosing func/method/class/module/file
    span: Span          # occurrence span (stored in edge metadata)
```
- `PythonASTVisitor` exposes `self.occurrences: list[OccurrenceRef]` and `self.abs_file_path: str` (absolute path of the visited file) after `visit()`.
- The visitor no longer emits `CALLS` or `INHERITS_FROM` directly; it collects `call` and `base` occurrences. It still emits structural nodes + `CONTAINS`/`DECLARES` and the existing `IMPORT`/`IMPORTS`/`RESOLVES_TO` import edges.

---

## Task 1: Extend core model (node & relation kinds)

**Files:**
- Modify: `src/graphlens/models/nodes.py:13-26`
- Modify: `src/graphlens/models/relations.py:9-19`
- Test: `tests/test_models_nodes.py`, `tests/test_models_relations.py`

**Interfaces:**
- Produces: `NodeKind.VARIABLE`, `NodeKind.ATTRIBUTE`, `NodeKind.TYPE_ALIAS`, `RelationKind.HAS_TYPE`.
- Note: `NodeKind.SYMBOL` stays for now (removed in Task 5) so the current visitor keeps compiling.

- [ ] **Step 1: Write the failing test**

In `tests/test_models_nodes.py` add:
```python
def test_new_node_kinds_exist():
    assert NodeKind.VARIABLE.value == "variable"
    assert NodeKind.ATTRIBUTE.value == "attribute"
    assert NodeKind.TYPE_ALIAS.value == "type_alias"
```
In `tests/test_models_relations.py` add:
```python
def test_has_type_relation_kind_exists():
    assert RelationKind.HAS_TYPE.value == "has_type"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models_nodes.py::test_new_node_kinds_exist tests/test_models_relations.py::test_has_type_relation_kind_exists -v`
Expected: FAIL with `AttributeError: VARIABLE` / `HAS_TYPE`.

- [ ] **Step 3: Add the enum members**

In `src/graphlens/models/nodes.py`, inside `NodeKind`, after `PARAMETER = "parameter"` add:
```python
    VARIABLE = "variable"
    ATTRIBUTE = "attribute"
    TYPE_ALIAS = "type_alias"
```
In `src/graphlens/models/relations.py`, inside `RelationKind`, after `INHERITS_FROM = "inherits_from"` add:
```python
    HAS_TYPE = "has_type"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models_nodes.py tests/test_models_relations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/graphlens/models/nodes.py src/graphlens/models/relations.py tests/test_models_nodes.py tests/test_models_relations.py
git commit -m "feat(model): add VARIABLE/ATTRIBUTE/TYPE_ALIAS nodes and HAS_TYPE relation"
```

---

## Task 2: `SpanIndex` location→node bridge

**Files:**
- Create: `src/graphlens/utils/span_index.py`
- Modify: `src/graphlens/utils/__init__.py:1-15` (export `SpanIndex`)
- Test: `tests/test_utils_span_index.py`

**Interfaces:**
- Consumes: `Span` (`graphlens.utils.span`), `GraphLens`, `Node`, `NodeKind`.
- Produces: `SpanIndex` (signature in Global Interfaces).

- [ ] **Step 1: Write the failing test**

Create `tests/test_utils_span_index.py`:
```python
from graphlens import GraphLens, Node, NodeKind
from graphlens.utils import SpanIndex
from graphlens.utils.span import Span


def _node(node_id, full, name_span):
    return Node(
        id=node_id,
        kind=NodeKind.FUNCTION,
        qualified_name=node_id,
        name=node_id,
        file_path="/abs/mod.py",
        span=full,
        metadata={"name_span": name_span},
    )


def test_at_matches_name_span_not_keyword():
    # function spans lines 1-5; its name 'foo' sits at line 1 col 5
    g = GraphLens()
    g.add_node(_node("foo", Span(1, 1, 5, 1), Span(1, 5, 1, 8)))
    idx = SpanIndex.from_graph(g)
    assert idx.at("/abs/mod.py", 1, 5) == "foo"
    assert idx.at("/abs/mod.py", 3, 1) is None  # body, not the name


def test_enclosing_returns_innermost():
    g = GraphLens()
    g.add_node(_node("outer", Span(1, 1, 10, 1), Span(1, 5, 1, 10)))
    g.add_node(_node("inner", Span(4, 5, 6, 1), Span(4, 9, 4, 14)))
    idx = SpanIndex.from_graph(g)
    assert idx.enclosing("/abs/mod.py", 5, 5) == "inner"
    assert idx.enclosing("/abs/mod.py", 2, 1) == "outer"


def test_missing_file_returns_none():
    idx = SpanIndex()
    assert idx.at("/nope.py", 1, 1) is None
    assert idx.enclosing("/nope.py", 1, 1) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_utils_span_index.py -v`
Expected: FAIL with `ImportError: cannot import name 'SpanIndex'`.

- [ ] **Step 3: Implement `SpanIndex`**

Create `src/graphlens/utils/span_index.py`:
```python
"""Map a source position (file + 1-based line/col) back to a graph node."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphlens.models.graph import GraphLens
    from graphlens.utils.span import Span

_Entry = tuple[str, "Span"]  # (node_id, span)


def _contains(span: Span, line: int, col: int) -> bool:
    after_start = (line, col) >= (span.start_line, span.start_col)
    before_end = (line, col) <= (span.end_line, span.end_col)
    return after_start and before_end


def _area(span: Span) -> tuple[int, int]:
    # Smaller = tighter. Compare by (line spread, col spread).
    return (span.end_line - span.start_line, span.end_col - span.start_col)


class SpanIndex:
    """Per-file lists of (node_id, span); supports innermost/name lookups."""

    def __init__(self) -> None:
        self._full: dict[str, list[_Entry]] = {}
        self._name: dict[str, list[_Entry]] = {}

    def add_full(self, file_path: str, node_id: str, span: Span) -> None:
        self._full.setdefault(file_path, []).append((node_id, span))

    def add_name(self, file_path: str, node_id: str, name_span: Span) -> None:
        self._name.setdefault(file_path, []).append((node_id, name_span))

    @classmethod
    def from_graph(cls, graph: GraphLens) -> SpanIndex:
        idx = cls()
        for node in graph.nodes.values():
            if not isinstance(node.file_path, str) or node.span is None:
                continue
            idx.add_full(node.file_path, node.id, node.span)
            name_span = node.metadata.get("name_span")
            if name_span is not None:
                idx.add_name(node.file_path, node.id, name_span)  # type: ignore[arg-type]
        return idx

    def _smallest_containing(
        self, table: dict[str, list[_Entry]], file_path: str,
        line: int, col: int,
    ) -> str | None:
        best_id: str | None = None
        best_area: tuple[int, int] | None = None
        for node_id, span in table.get(file_path, ()):
            if not _contains(span, line, col):
                continue
            area = _area(span)
            if best_area is None or area < best_area:
                best_area, best_id = area, node_id
        return best_id

    def enclosing(self, file_path: str, line: int, col: int) -> str | None:
        return self._smallest_containing(self._full, file_path, line, col)

    def at(self, file_path: str, line: int, col: int) -> str | None:
        return self._smallest_containing(self._name, file_path, line, col)
```

- [ ] **Step 4: Export `SpanIndex`**

In `src/graphlens/utils/__init__.py` add the import and `__all__` entry:
```python
from graphlens.utils.span_index import SpanIndex
```
Add `"SpanIndex",` to `__all__` (keep alphabetical).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_utils_span_index.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/graphlens/utils/span_index.py src/graphlens/utils/__init__.py tests/test_utils_span_index.py
git commit -m "feat(utils): add SpanIndex location-to-node bridge"
```

---

## Task 3: `SymbolResolver` contract + DTOs

**Files:**
- Create: `src/graphlens/contracts/resolver.py`
- Modify: `src/graphlens/contracts/__init__.py:1-18` (export `SymbolResolver`, `ResolvedRef`, `Occurrence`)
- Test: `tests/test_contracts_resolver.py`

**Interfaces:**
- Produces: `SymbolResolver`, `ResolvedRef`, `Occurrence` (signatures in Global Interfaces).

- [ ] **Step 1: Write the failing test**

Create `tests/test_contracts_resolver.py`:
```python
from pathlib import Path

import pytest

from graphlens.contracts import Occurrence, ResolvedRef, SymbolResolver


def test_resolver_is_abstract():
    with pytest.raises(TypeError):
        SymbolResolver()  # type: ignore[abstract]


def test_dtos_are_frozen():
    ref = ResolvedRef(
        full_name="pkg.mod.foo", file_path=Path("/abs/mod.py"),
        line=1, col=5, kind="function", origin="internal",
    )
    occ = Occurrence(
        file_path=Path("/abs/mod.py"), line=3, col=1,
        is_definition=False, access="call",
    )
    with pytest.raises(AttributeError):
        ref.full_name = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        occ.access = "x"  # type: ignore[misc]


def test_concrete_subclass_must_implement_all():
    class Partial(SymbolResolver):
        def prepare(self, project_root, files):  # noqa: ANN001, ANN201
            ...
    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_contracts_resolver.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the contract**

Create `src/graphlens/contracts/resolver.py`:
```python
"""SymbolResolver contract: a type-aware resolution backend for one language."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ResolvedRef:
    """A symbol resolved to its definition. Coordinates are 1-based."""

    full_name: str
    file_path: Path | None
    line: int
    col: int
    kind: str
    origin: str


@dataclass(frozen=True)
class Occurrence:
    """A single appearance of a symbol. Coordinates are 1-based."""

    file_path: Path
    line: int
    col: int
    is_definition: bool
    access: str


class SymbolResolver(ABC):
    """
    Resolves source positions to definitions for one language.

    Lets an adapter build precise CALLS/REFERENCES/HAS_TYPE/INHERITS_FROM
    edges. All coordinates are 1-based (line and column); an implementation
    converts to its engine's convention internally.
    """

    @abstractmethod
    def prepare(self, project_root: Path, files: list[Path]) -> None:
        """Set up the engine for a project before any queries."""
        ...

    @abstractmethod
    def definition_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        """Resolve the symbol at a position to its definition (cross-file)."""
        ...

    @abstractmethod
    def infer_type_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        """Infer the type of the expression at a position."""
        ...

    @abstractmethod
    def references_to(
        self, file: Path, line: int, col: int
    ) -> list[Occurrence]:
        """Return all references to the symbol at a position."""
        ...
```

- [ ] **Step 4: Export from contracts package**

In `src/graphlens/contracts/__init__.py` add:
```python
from graphlens.contracts.resolver import (
    Occurrence,
    ResolvedRef,
    SymbolResolver,
)
```
Add `"Occurrence"`, `"ResolvedRef"`, `"SymbolResolver"` to `__all__` (keep alphabetical).

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_contracts_resolver.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/graphlens/contracts/resolver.py src/graphlens/contracts/__init__.py tests/test_contracts_resolver.py
git commit -m "feat(contracts): add SymbolResolver ABC + ResolvedRef/Occurrence DTOs"
```

---

## Task 4: `JediResolver` (Python type-aware backend)

**Files:**
- Create: `packages/graphlens-python/src/graphlens_python/_resolver.py`
- Modify: `packages/graphlens-python/pyproject.toml` (add `jedi>=0.19.2`)
- Test: `packages/graphlens-python/tests/test_resolver.py`

**Interfaces:**
- Consumes: `SymbolResolver`, `ResolvedRef`, `Occurrence` (Task 3).
- Produces: `JediResolver(stdlib_names: frozenset[str])` implementing `SymbolResolver`.

- [ ] **Step 1: Add the jedi dependency**

In `packages/graphlens-python/pyproject.toml`, add to `[project].dependencies` (next to `tree-sitter`):
```toml
    "jedi>=0.19.2",
```
Run `uv sync --all-packages --all-groups` to install.

- [ ] **Step 2: Write the failing test**

Create `packages/graphlens-python/tests/test_resolver.py`:
```python
from pathlib import Path

import pytest

from graphlens_python._resolver import JediResolver


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "util.py").write_text(
        "def helper(x):\n    return x\n"
    )
    (tmp_path / "pkg" / "main.py").write_text(
        "from pkg.util import helper\n"
        "import os\n"
        "\n"
        "def run():\n"
        "    helper(1)\n"
        "    os.getcwd()\n"
    )
    return tmp_path


def _resolver(proj: Path) -> JediResolver:
    r = JediResolver(stdlib_names=frozenset({"os"}))
    r.prepare(proj, list(proj.rglob("*.py")))
    return r


def test_resolves_imported_call_to_internal_definition(proj):
    r = _resolver(proj)
    # 'helper' callee at main.py line 5, col 5 (1-based)
    ref = r.definition_at(proj / "pkg" / "main.py", 5, 5)
    assert ref is not None
    assert ref.full_name.endswith("util.helper")
    assert ref.file_path == proj / "pkg" / "util.py"
    assert ref.origin == "internal"
    assert ref.line == 1  # def helper on line 1
    assert ref.col == 5   # name 'helper' is 1-based col 5


def test_classifies_stdlib_origin(proj):
    r = _resolver(proj)
    # 'os' at main.py line 6 col 5
    ref = r.definition_at(proj / "pkg" / "main.py", 6, 5)
    assert ref is not None
    assert ref.origin == "stdlib"


def test_missing_resolution_returns_none(proj):
    r = _resolver(proj)
    ref = r.definition_at(proj / "pkg" / "main.py", 99, 99)
    assert ref is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest packages/graphlens-python/tests/test_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: graphlens_python._resolver`.

- [ ] **Step 4: Implement `JediResolver`**

Create `packages/graphlens-python/src/graphlens_python/_resolver.py`:
```python
"""jedi-backed SymbolResolver for Python — precise cross-file resolution."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import jedi
from graphlens.contracts import Occurrence, ResolvedRef, SymbolResolver

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("graphlens_python")

# jedi: line 1-based, column 0-based. graphlens: both 1-based.


class JediResolver(SymbolResolver):
    """Resolve Python symbols via jedi. Never raises; returns None/[] on miss."""

    def __init__(self, stdlib_names: frozenset[str]) -> None:
        self._stdlib_names = stdlib_names
        self._project: jedi.Project | None = None
        self._root: Path | None = None

    def prepare(self, project_root: Path, files: list[Path]) -> None:
        self._root = project_root
        try:
            self._project = jedi.Project(str(project_root))
        except Exception:  # noqa: BLE001 — never raise out of the resolver
            logger.warning("jedi.Project failed for %s", project_root)
            self._project = None

    def _script(self, file: Path) -> jedi.Script | None:
        if self._project is None:
            return None
        try:
            return jedi.Script(path=str(file), project=self._project)
        except Exception:  # noqa: BLE001
            return None

    def definition_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        script = self._script(file)
        if script is None:
            return None
        try:
            names = script.goto(line, col - 1, follow_imports=True)
        except Exception:  # noqa: BLE001
            return None
        return self._to_ref(names[0]) if names else None

    def infer_type_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        script = self._script(file)
        if script is None:
            return None
        try:
            names = script.infer(line, col - 1)
        except Exception:  # noqa: BLE001
            return None
        return self._to_ref(names[0]) if names else None

    def references_to(
        self, file: Path, line: int, col: int
    ) -> list[Occurrence]:
        script = self._script(file)
        if script is None:
            return []
        try:
            names = script.get_references(line, col - 1, scope="project")
        except Exception:  # noqa: BLE001
            return []
        out: list[Occurrence] = []
        for n in names:
            if n.module_path is None or n.line is None:
                continue
            out.append(
                Occurrence(
                    file_path=n.module_path,
                    line=n.line,
                    col=(n.column or 0) + 1,
                    is_definition=n.is_definition(),
                    access="unknown",
                )
            )
        return out

    def _to_ref(self, name: object) -> ResolvedRef:
        # name is a jedi.api.classes.Name
        in_builtin = bool(name.in_builtin_module())  # type: ignore[attr-defined]
        module_path = name.module_path  # type: ignore[attr-defined]
        full_name = name.full_name or name.name  # type: ignore[attr-defined]
        return ResolvedRef(
            full_name=full_name or "",
            file_path=module_path,
            line=name.line or 1,  # type: ignore[attr-defined]
            col=(name.column or 0) + 1,  # type: ignore[attr-defined]
            kind=name.type,  # type: ignore[attr-defined]
            origin=self._classify(module_path, full_name, in_builtin),
        )

    def _classify(
        self, module_path: Path | None, full_name: str | None,
        in_builtin: bool,
    ) -> str:
        if module_path is None or in_builtin:
            return "stdlib"
        if self._root is not None:
            try:
                module_path.relative_to(self._root)
                return "internal"
            except ValueError:
                pass
        parts = module_path.parts
        if "site-packages" in parts or "dist-packages" in parts:
            return "third_party"
        top = (full_name or "").split(".")[0]
        if top in self._stdlib_names:
            return "stdlib"
        return "unknown"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest packages/graphlens-python/tests/test_resolver.py -v`
Expected: PASS (3 tests). If `test_resolves_imported_call_to_internal_definition` reports a different `col`, confirm the +1 conversion and the 1-based input contract.

- [ ] **Step 6: Commit**

```bash
git add packages/graphlens-python/pyproject.toml packages/graphlens-python/src/graphlens_python/_resolver.py packages/graphlens-python/tests/test_resolver.py
git commit -m "feat(python): add jedi-backed JediResolver implementing SymbolResolver"
```

---

## Task 5: Rework the visitor — structural nodes, name_span, occurrence collection

**Files:**
- Modify: `src/graphlens/models/nodes.py` (remove `SYMBOL`)
- Modify: `packages/graphlens-python/src/graphlens_python/_visitor.py`
- Test: `packages/graphlens-python/tests/test_visitor.py`

**Interfaces:**
- Produces: `OccurrenceRef` dataclass; `PythonASTVisitor.occurrences: list[OccurrenceRef]`; `PythonASTVisitor.abs_file_path: str`; structural nodes carry `metadata["name_span"]`. No `CALLS`/`INHERITS_FROM` emitted by the visitor.
- Consumes: `VARIABLE`, `ATTRIBUTE`, `TYPE_ALIAS`, `Span` (Task 1).

> This is a large task; it has four TDD cycles (5.1–5.4). Commit after each.

### 5.1 — `name_span` on structural nodes

- [ ] **Step 1: Failing test**

In `packages/graphlens-python/tests/test_visitor.py` add (using the existing `parse_and_visit` conftest helper which returns the populated `GraphLens`):
```python
def test_class_node_carries_name_span(parse_and_visit):
    graph = parse_and_visit("class Foo:\n    pass\n")
    cls = next(n for n in graph.nodes.values() if n.kind.value == "class")
    ns = cls.metadata["name_span"]
    # 'Foo' starts at line 1, col 7 (1-based)
    assert (ns.start_line, ns.start_col) == (1, 7)
```

- [ ] **Step 2: Verify it fails** — `KeyError: 'name_span'`.

- [ ] **Step 3: Thread a name node into `_make_node`**

Change `_make_node` signature to accept the identifier node and record its span:
```python
def _make_node(
    self,
    kind: NodeKind,
    qualified_name: str,
    name: str,
    ts_node: TSNode | None = None,
    metadata: dict[str, object] | None = None,
    name_node: TSNode | None = None,
) -> Node:
    md = dict(metadata or {})
    if name_node is not None:
        name_span = _make_span(name_node)
        if name_span is not None:
            md["name_span"] = name_span
    return Node(
        id=make_node_id(self._ctx.project_name, qualified_name, kind.value),
        kind=kind,
        qualified_name=qualified_name,
        name=name,
        file_path=str(self._ctx.file_path),
        span=_make_span(ts_node) if ts_node else None,
        metadata=md,
    )
```
Pass `name_node=name_node` from `_handle_class`, `_handle_function`, `_extract_parameters`, and (new) variable/attribute/type-alias handlers. (In `_handle_class`/`_handle_function`, `name_node` is the already-located identifier child.)

- [ ] **Step 4: Verify it passes.**

- [ ] **Step 5: Commit** — `feat(python): record name_span on structural nodes`.

### 5.2 — Remove `SYMBOL`; collect `call` occurrences instead of CALLS→SYMBOL

- [ ] **Step 1: Failing test**
```python
def test_calls_collected_as_occurrences_not_symbol(parse_and_visit_visitor):
    # parse_and_visit_visitor returns (graph, visitor) — see conftest update
    graph, visitor = parse_and_visit_visitor(
        "def a():\n    b()\n\ndef b():\n    pass\n"
    )
    assert all(n.kind.value != "symbol" for n in graph.nodes.values())
    roles = [o.role for o in visitor.occurrences]
    assert "call" in roles
```
Add a `parse_and_visit_visitor` helper to `packages/graphlens-python/tests/conftest.py` that mirrors `parse_and_visit` but returns `(graph, visitor)`.

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Add `OccurrenceRef`, occurrence list, and rework call handling**

At module top of `_visitor.py` add:
```python
@dataclass(frozen=True)
class OccurrenceRef:
    """A use-site the resolver will bind to a definition. 1-based coords."""

    role: str           # call|read|write|annotation|base
    line: int
    col: int
    enclosing_id: str
    span: Span
```
In `PythonASTVisitor.__init__` add:
```python
        self.occurrences: list[OccurrenceRef] = []
        self.abs_file_path: str = str(ctx.file_path)
```
Replace `_extract_calls`/`_find_calls_in_node` so a call records an occurrence at the **callee name position** (not a SYMBOL node):
```python
def _extract_calls(self, body: TSNode, caller_id: str) -> None:
    for child in body.children:
        self._find_calls_in_node(child, caller_id)

def _find_calls_in_node(self, node: TSNode, caller_id: str) -> None:
    if node.type == "call":
        func_node = next(
            (c for c in node.children
             if c.type in ("identifier", "attribute")),
            None,
        )
        if func_node is not None:
            name_node = (
                func_node.children[-1]
                if func_node.type == "attribute" else func_node
            )
            self._record_occurrence("call", name_node, caller_id)
    if node.type not in (
        "function_definition", "class_definition", "decorated_definition",
    ):
        for child in node.children:
            self._find_calls_in_node(child, caller_id)

def _record_occurrence(
    self, role: str, name_node: TSNode, enclosing_id: str
) -> None:
    span = _make_span(name_node)
    if span is None:
        return
    self.occurrences.append(
        OccurrenceRef(
            role=role,
            line=span.start_line,
            col=span.start_col,
            enclosing_id=enclosing_id,
            span=span,
        )
    )
```
Delete the old SYMBOL-creation block entirely. Then remove `SYMBOL = "symbol"` from `NodeKind` in `src/graphlens/models/nodes.py`. Update any existing visitor test that asserted on SYMBOL nodes.

- [ ] **Step 4: Verify it passes** (and run the full visitor suite: `uv run pytest packages/graphlens-python/tests/test_visitor.py -v`).

- [ ] **Step 5: Commit** — `feat(python): collect call occurrences, drop SYMBOL nodes`.

### 5.3 — Collect `base` and `annotation` occurrences

- [ ] **Step 1: Failing test**
```python
def test_base_and_annotation_occurrences(parse_and_visit_visitor):
    graph, visitor = parse_and_visit_visitor(
        "class Base:\n    pass\n\n"
        "class Sub(Base):\n    pass\n\n"
        "def f(x: Base) -> Base:\n    pass\n"
    )
    roles = [o.role for o in visitor.occurrences]
    assert "base" in roles
    assert "annotation" in roles
```

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Record base + annotation occurrences**

In `_handle_class`, replace the `INHERITS_FROM`→EXTERNAL_SYMBOL emission loop with base-occurrence recording. For each base entry, locate its name node in the `argument_list` and call `self._record_occurrence("base", base_name_node, class_node.id)`. (Keep extracting `bases` text for metadata, but do not emit `INHERITS_FROM` here — the adapter resolves it.) When building the CLASS node, also set `metadata["is_enum"] = any(b.rsplit(".", 1)[-1] in {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"} for b in bases)` (spec §3.1 models enums as CLASS + flag).

In `_handle_function` return annotation and in `_extract_parameters` typed params, when an annotation `type` node exists, record an annotation occurrence on the type's leading identifier:
```python
def _record_annotation(self, type_node: TSNode, enclosing_id: str) -> None:
    ident = _first_identifier(type_node)
    if ident is not None:
        self._record_occurrence("annotation", ident, enclosing_id)
```
Add helper:
```python
def _first_identifier(node: TSNode) -> TSNode | None:
    if node.type == "identifier":
        return node
    for child in node.children:
        found = _first_identifier(child)
        if found is not None:
            return found
    return None
```
Call `self._record_annotation(type_node, func_node.id)` for the return type, and `self._record_annotation(type_node, param_node.id)` for each typed parameter.

- [ ] **Step 4: Verify it passes.**

- [ ] **Step 5: Commit** — `feat(python): collect base + annotation occurrences`.

### 5.4 — Structural nodes: `VARIABLE`, `ATTRIBUTE`, `TYPE_ALIAS` + read/write occurrences

- [ ] **Step 1: Failing test**
```python
def test_module_variable_node_and_write_occurrence(parse_and_visit_visitor):
    graph, visitor = parse_and_visit_visitor("CONST = 1\nx = CONST\n")
    kinds = {n.kind.value for n in graph.nodes.values()}
    assert "variable" in kinds
    roles = [o.role for o in visitor.occurrences]
    assert "write" in roles   # assignment target
    assert "read" in roles    # CONST on the rhs


def test_type_alias_node(parse_and_visit_visitor):
    graph, _ = parse_and_visit_visitor(
        "from typing import TypeAlias\nVector: TypeAlias = list[float]\n"
    )
    assert any(n.kind.value == "type_alias" for n in graph.nodes.values())
```

- [ ] **Step 2: Verify it fails.**

- [ ] **Step 3: Handle assignments**

Add `_visit_expression_statement` handling for `assignment` children at module/class/function scope:
- A bare `assignment` `LHS = RHS`: create a `VARIABLE` node (or `ATTRIBUTE` when `_kind_stack[-1] == NodeKind.CLASS`, or when LHS is `self.<attr>`) keyed `f"{scope}.{name}"`, with `metadata["is_constant"] = name.isupper()`; record a `write` occurrence on the LHS identifier (enclosing = current container) and `read` occurrences on RHS identifiers (via `_first_identifier` walk over non-call RHS leaves).
- A typed `assignment` whose annotation text is exactly `TypeAlias` (or RHS is a typing construct with `: TypeAlias`): create a `TYPE_ALIAS` node instead of `VARIABLE`; record an `annotation` occurrence on the RHS leading identifier.

Concretely:
```python
def _visit_expression_statement(self, node: TSNode) -> None:
    for child in node.children:
        if child.type == "assignment":
            self._handle_assignment(child)
        else:
            self.visit(child)

def _handle_assignment(self, node: TSNode) -> None:
    lhs = node.children[0]
    annotation = next(
        (c for c in node.children if c.type == "type"), None
    )
    rhs = node.children[-1] if node.children[-1] is not lhs else None
    name_node = _first_identifier(lhs)
    if name_node is None:
        return
    name = _node_text(name_node)
    is_alias = annotation is not None and _node_text(annotation) == "TypeAlias"
    in_class = self._kind_stack[-1] == NodeKind.CLASS
    kind = (
        NodeKind.TYPE_ALIAS if is_alias
        else NodeKind.ATTRIBUTE if in_class
        else NodeKind.VARIABLE
    )
    qname = f"{self._scope_stack[-1]}.{name}"
    var_node = self._make_node(
        kind, qname, name, node,
        metadata={"is_constant": name.isupper()},
        name_node=name_node,
    )
    self._add_node_with_relation(var_node, RelationKind.DECLARES)
    self._record_occurrence("write", name_node, self._container_stack[-1])
    if rhs is not None and not is_alias:
        self._record_reads(rhs)
    elif is_alias and rhs is not None:
        ident = _first_identifier(rhs)
        if ident is not None:
            self._record_occurrence(
                "annotation", ident, self._container_stack[-1]
            )

def _record_reads(self, node: TSNode) -> None:
    if node.type == "call":
        # calls are handled as call occurrences elsewhere; still read args
        for c in node.children:
            if c.type == "argument_list":
                self._record_reads(c)
        return
    if node.type == "identifier":
        self._record_occurrence("read", node, self._container_stack[-1])
        return
    for child in node.children:
        self._record_reads(child)
```
Note: module/function-body assignments are reached because `_visit_module` and the function-body visit walk children; ensure `_handle_function`'s body loop also visits `expression_statement` children (extend the body child-type filter to include `"expression_statement"`).

- [ ] **Step 4: Verify it passes.** Run the full visitor suite.

- [ ] **Step 5: Commit** — `feat(python): model variables/attributes/type-aliases + read/write occurrences`.

---

## Task 6: Adapter resolution pass (occurrence-driven edges)

**Files:**
- Modify: `packages/graphlens-python/src/graphlens_python/_adapter.py`
- Test: `packages/graphlens-python/tests/test_adapter.py`

**Interfaces:**
- Consumes: `SpanIndex` (Task 2), `JediResolver` (Task 4), `OccurrenceRef` + `visitor.occurrences`/`visitor.abs_file_path` (Task 5).
- Produces: resolved `CALLS`/`REFERENCES`/`HAS_TYPE`/`INHERITS_FROM` edges to real nodes (or `EXTERNAL_SYMBOL` fallback).

- [ ] **Step 1: Write the failing tests**

In `packages/graphlens-python/tests/test_adapter.py` add (uses `sample_python_project` fixture or build a fresh `tmp_path` project with cross-file calls):
```python
def _edges(graph, kind):
    return [r for r in graph.relations if r.kind.value == kind]


def test_calls_resolve_to_real_function_node(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "util.py").write_text("def helper():\n    return 1\n")
    (tmp_path / "pkg" / "main.py").write_text(
        "from pkg.util import helper\n\ndef run():\n    helper()\n"
    )
    from graphlens_python import PythonAdapter
    graph = PythonAdapter().analyze(tmp_path)

    # no SYMBOL nodes anymore
    assert all(n.kind.value != "symbol" for n in graph.nodes.values())
    helper = next(
        n for n in graph.nodes.values()
        if n.kind.value == "function" and n.name == "helper"
    )
    calls = _edges(graph, "calls")
    assert any(r.target_id == helper.id for r in calls)


def test_has_type_edge_for_annotation(tmp_path):
    (tmp_path / "m.py").write_text(
        "class C:\n    pass\n\ndef f(x: C) -> None:\n    pass\n"
    )
    from graphlens_python import PythonAdapter
    graph = PythonAdapter().analyze(tmp_path)
    c = next(n for n in graph.nodes.values()
             if n.kind.value == "class" and n.name == "C")
    assert any(r.target_id == c.id for r in _edges(graph, "has_type"))


def test_inherits_from_resolves_internal_class(tmp_path):
    (tmp_path / "m.py").write_text(
        "class Base:\n    pass\n\nclass Sub(Base):\n    pass\n"
    )
    from graphlens_python import PythonAdapter
    graph = PythonAdapter().analyze(tmp_path)
    base = next(n for n in graph.nodes.values()
               if n.kind.value == "class" and n.name == "Base")
    inh = _edges(graph, "inherits_from")
    assert any(r.target_id == base.id for r in inh)


def test_variable_read_write_references(tmp_path):
    (tmp_path / "m.py").write_text(
        "CONST = 1\n\ndef f():\n    return CONST\n"
    )
    from graphlens_python import PythonAdapter
    graph = PythonAdapter().analyze(tmp_path)
    refs = _edges(graph, "references")
    accesses = {r.metadata.get("access") for r in refs}
    assert "write" in accesses  # CONST = 1
    assert "read" in accesses   # return CONST
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/graphlens-python/tests/test_adapter.py -k "resolve or has_type or inherits" -v`
Expected: FAIL (CALLS still point at SYMBOL / no HAS_TYPE).

- [ ] **Step 3: Wire the resolution pass into `_analyze_root`**

In `_adapter.py` add runtime imports near the top:
```python
from pathlib import Path

from graphlens.utils import SpanIndex

from graphlens_python._resolver import JediResolver
from graphlens_python._visitor import OccurrenceRef  # noqa: F401 (type)
```
Give `PythonAdapter.__init__` an injectable resolver (mirrors `dep_parsers`):
```python
    def __init__(
        self,
        dep_parsers: list[DependencyFileParser] | None = None,
        resolver: SymbolResolver | None = None,
    ) -> None:
        self._dep_parsers = (
            dep_parsers if dep_parsers is not None
            else PYTHON_DEFAULT_DEP_PARSERS
        )
        self._resolver = (
            resolver if resolver is not None
            else JediResolver(stdlib_names=_STDLIB)
        )
```
(Import `SymbolResolver` from `graphlens.contracts` under `TYPE_CHECKING`.)
Thread `self._resolver` into both `_analyze_root(...)` calls in `analyze()` as a new trailing argument.

In `_analyze_root`, add `resolver: SymbolResolver` to the signature, accumulate occurrences during the file loop, and run the resolution pass after it:
```python
    all_occurrences: list[tuple[str, OccurrenceRef]] = []
    # ... inside the existing `for file in files:` loop, after visitor.visit():
        all_occurrences.extend(
            (visitor.abs_file_path, o) for o in visitor.occurrences
        )

    # ... after the file loop, before PROJECT--CONTAINS-->modules:
    span_index = SpanIndex.from_graph(graph)
    resolver.prepare(py_root, files)
    _resolve_occurrences(
        graph, project_name, resolver, span_index, all_occurrences
    )
```
Add the resolution helpers at module level:
```python
_ROLE_TO_KIND = {
    "call": RelationKind.CALLS,
    "base": RelationKind.INHERITS_FROM,
    "annotation": RelationKind.HAS_TYPE,
    "read": RelationKind.REFERENCES,
    "write": RelationKind.REFERENCES,
}


def _ensure_external_symbol(
    graph: GraphLens, project_name: str, qname: str, origin: str
) -> str:
    sym_id = make_node_id(
        project_name, qname, NodeKind.EXTERNAL_SYMBOL.value
    )
    if sym_id not in graph.nodes:
        graph.add_node(
            Node(
                id=sym_id,
                kind=NodeKind.EXTERNAL_SYMBOL,
                qualified_name=qname,
                name=qname.rsplit(".", maxsplit=1)[-1],
                metadata={"origin": origin},
            )
        )
    return sym_id


def _resolve_occurrences(
    graph: GraphLens,
    project_name: str,
    resolver: SymbolResolver,
    span_index: SpanIndex,
    occurrences: list[tuple[str, OccurrenceRef]],
) -> None:
    for abs_path, occ in occurrences:
        rel_kind = _ROLE_TO_KIND[occ.role]
        ref = resolver.definition_at(Path(abs_path), occ.line, occ.col)
        if ref is None:
            continue
        target_id: str | None = None
        if ref.origin == "internal" and ref.file_path is not None:
            target_id = span_index.at(
                str(ref.file_path), ref.line, ref.col
            )
        if target_id is None:
            target_id = _ensure_external_symbol(
                graph, project_name, ref.full_name or occ.role, ref.origin
            )
        metadata: dict[str, object] = {"span": occ.span}
        if occ.role in ("read", "write"):
            metadata["access"] = occ.role
        graph.add_relation(
            Relation(
                source_id=occ.enclosing_id,
                target_id=target_id,
                kind=rel_kind,
                metadata=metadata,
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest packages/graphlens-python/tests/test_adapter.py -v`
Expected: PASS. Fix any pre-existing adapter tests that asserted on SYMBOL/old CALLS shape.

- [ ] **Step 5: Run the full Python adapter suite + coverage**

Run: `task python:test` (or `uv run pytest packages/graphlens-python/tests --cov=graphlens_python`)
Expected: PASS, coverage 100%. Add targeted tests for read/write `REFERENCES` (`access` metadata) and the `EXTERNAL_SYMBOL` fallback (e.g. a call to an unresolved third-party symbol) to reach 100%.

- [ ] **Step 6: Commit**

```bash
git add packages/graphlens-python/src/graphlens_python/_adapter.py packages/graphlens-python/tests/test_adapter.py
git commit -m "feat(python): occurrence-driven resolution pass — resolved CALLS/REFERENCES/HAS_TYPE/INHERITS_FROM"
```

---

## Task 7: Migration — docs, exports, full verification

**Files:**
- Modify: `CLAUDE.md` (§4, §5, §9)
- Modify: `CHANGELOG.md`
- Modify: `packages/graphlens-python/src/graphlens_python/__init__.py` (export `JediResolver`)
- Test: full suite + lint + types

**Interfaces:** none new — documentation and final gate.

- [ ] **Step 1: Update CLAUDE.md**

- **§4** retitle to "Tree-sitter + type-aware resolver": tree-sitter for structure + occurrence roles + spans; a `SymbolResolver` (jedi for Python) for resolution. Keep the parser-singleton guidance.
- **§5** note the visitor now also collects `OccurrenceRef`s (call/read/write/annotation/base) with `name_span` on structural nodes; it no longer emits `CALLS`/`INHERITS_FROM` directly.
- **§9** note `origin` for code references comes from the resolver's `ResolvedRef.origin` (import-manifest pre-pass still classifies imports); add the `SpanIndex` location→node bridge to the resolution pipeline description.

- [ ] **Step 2: Update CHANGELOG.md**

Add an `Unreleased` section:
```markdown
## [Unreleased]

### Changed (breaking)
- Graph model: removed `NodeKind.SYMBOL`; added `VARIABLE`, `ATTRIBUTE`,
  `TYPE_ALIAS` node kinds and the `HAS_TYPE` relation kind.
- Python adapter: `CALLS`/`INHERITS_FROM` now resolve to real declaration
  nodes (via jedi); added resolved `REFERENCES` (read/write) and `HAS_TYPE`.

### Added
- Core `SpanIndex` (location→node bridge) and `SymbolResolver` contract
  with `ResolvedRef`/`Occurrence` DTOs.
- `graphlens-python` now depends on `jedi>=0.19.2`.
```

- [ ] **Step 3: Export `JediResolver`**

In `packages/graphlens-python/src/graphlens_python/__init__.py` add `JediResolver` to imports and `__all__`.

- [ ] **Step 4: Full verification gate**

Run, and confirm each passes:
```bash
task lint
task tests
```
Expected: ruff clean, ty clean, all tests pass, core coverage ≥90%, Python adapter coverage 100%.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md CHANGELOG.md packages/graphlens-python/src/graphlens_python/__init__.py
git commit -m "docs(python): document resolver redesign; export JediResolver; changelog"
```

---

## Notes for the implementer

- **Coordinate convention is the #1 bug source.** graphlens is 1-based line AND col everywhere. Only `JediResolver` touches jedi's 0-based column (subtract 1 on input, add 1 on output). `_make_span` already converts tree-sitter's 0-based points (+1).
- **Absolute paths** key the `SpanIndex` and flow through occurrences: structural nodes' `file_path` is `str(ctx.file_path)` (absolute), jedi's `module_path` is absolute — they match. (Pre-existing: `FILE` nodes use a project-relative path and are not used as resolution targets.)
- **jedi needs a correct `Project`/sys.path** for internal resolution; tests build a real package under `tmp_path` so imports resolve.
- **Never let the resolver raise** — every jedi call is wrapped; misses return `None`/`[]`, and the adapter skips or falls back to `EXTERNAL_SYMBOL`.
- **Relations are not deduped** (existing behavior); multiple identical call-sites produce multiple `CALLS` edges. Consumers dedup.

