---
sidebar_position: 4
---

# Contracts

The abstract base classes that extension points implement. They live in
`graphlens.contracts` and the most common ones are re-exported from the
top-level package.

```python
from graphlens import (
    LanguageAdapter,
    DependencyFileParser,
    GraphBackend,
    ProjectReader,
    DiscoveredProject,
    BoundaryRef,
)
```

## LanguageAdapter

The contract every language adapter implements. See
[Writing an adapter](../adapters/writing-an-adapter.md) for a walkthrough.

```python
class LanguageAdapter(ABC):
    @abstractmethod
    def language(self) -> str: ...

    @abstractmethod
    def can_handle(self, project_root: str | Path) -> bool: ...

    @abstractmethod
    def analyze(
        self,
        project_root: str | Path,
        files: list[Path] | None = None,
        *,
        strict: bool = False,
    ) -> GraphLens: ...

    # Provided defaults:
    def file_extensions(self) -> set[str]: ...
    def collect_files(self, project_root: str | Path) -> list[Path]: ...
```

- `can_handle` must return `True` for multi-language projects even when the
  marker file lives in a sub-directory.
- `analyze` with `strict=True` raises [`AdapterError`](./exceptions.md) when the
  resolver status is not `ok`.
- `collect_files` has a default implementation driven by `file_extensions()`.

## SymbolResolver

The type-aware resolution backend (in `graphlens.contracts.resolver`). All
coordinates are **1-based**. Implementations must **never raise** — every method
returns `None` or `[]` on failure.

```python
class SymbolResolver(ABC):
    @abstractmethod
    def prepare(self, project_root: Path, files: list[Path]) -> None: ...

    @abstractmethod
    def definition_at(self, file: Path, line: int, col: int) -> ResolvedRef | None: ...

    @abstractmethod
    def infer_type_at(self, file: Path, line: int, col: int) -> ResolvedRef | None: ...

    @abstractmethod
    def references_to(self, file: Path, line: int, col: int) -> list[Occurrence]: ...

    def status(self) -> ResolverStatus: ...     # defaults to OK
```

Supporting dataclasses:

```python
@dataclass
class ResolvedRef:
    full_name: str
    file_path: str
    line: int
    col: int
    kind: str
    origin: str            # 'stdlib' | 'internal' | 'third_party' | 'unknown'

@dataclass
class Occurrence:
    file_path: str
    line: int
    col: int
    is_definition: bool
    access: str
```

The resolution pass calls `definition_at` for every occurrence role;
`infer_type_at` is part of the contract for type inference but is not invoked by
the current pass.

## DependencyFileParser

Extracts declared third-party package names from a manifest. One parser per file
format; compose them into a `<LANG>_DEFAULT_DEP_PARSERS` list.

```python
class DependencyFileParser(ABC):
    @abstractmethod
    def can_parse(self, project_root: Path) -> bool: ...

    @abstractmethod
    def parse(self, project_root: Path) -> frozenset[str]: ...
```

- Include dev/test groups so test imports classify as `third_party`.
- Return `frozenset()` on **any** error — never raise.
- Normalize names with `normalize_pkg_name()`.

## ProjectReader

Discovers projects and enumerates their source files.

```python
class ProjectReader(ABC):
    @abstractmethod
    def discover(self, root: Path) -> list[DiscoveredProject]: ...

@dataclass
class DiscoveredProject:
    root: Path
    language: str
    files: list[Path] = []
```

## GraphBackend

The persistence contract — implement it to store a graph somewhere (a database,
a file, a service).

```python
class GraphBackend(ABC):
    @abstractmethod
    def store(self, graph: GraphLens) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...
```

## BoundaryRef

A language-agnostic descriptor of a cross-language port, emitted by adapters
before a [`BOUNDARY`](../graph-model/boundaries.md) node is created.

```python
@dataclass
class BoundaryRef:
    mechanism: str                       # 'http' | 'grpc' | 'queue' | 'temporal'
    role: str                            # 'server' (exposes) | 'client' (consumes)
    key: str                             # normalized match key, e.g. 'GET /users/{}'
    line: int
    col: int
    confidence: float = 1.0
    detail: Mapping[str, str] = {}
```
