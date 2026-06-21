---
sidebar_position: 2
---

# Models

The data types that make up a graph. All are frozen dataclasses, importable from
the top-level package.

```python
from graphlens import (
    Node, NodeKind,
    Relation, RelationKind,
    GraphDiff,
)
from graphlens import make_node_id, make_boundary_id, normalize_http_path
```

## Node

```python
@dataclass(frozen=True)
class Node:
    id: str
    kind: NodeKind
    qualified_name: str
    name: str
    file_path: str | None = None
    span: Span | None = None
    metadata: dict[str, object] = {}
```

See [Node kinds](../graph-model/nodes.md) for the full vocabulary and ID scheme.

## NodeKind

An enum with the members:

`PROJECT` · `MODULE` · `FILE` · `CLASS` · `FUNCTION` · `METHOD` ·
`PARAMETER` · `IMPORT` · `DEPENDENCY` · `EXTERNAL_SYMBOL` · `VARIABLE` ·
`ATTRIBUTE` · `TYPE_ALIAS` · `BOUNDARY`

## Relation

```python
@dataclass(frozen=True)
class Relation:
    source_id: str
    target_id: str
    kind: RelationKind
    metadata: dict[str, object] = {}
```

See [Relation kinds](../graph-model/relations.md) for the full vocabulary.

## RelationKind

An enum with the members:

`CONTAINS` · `DECLARES` · `IMPORTS` · `CALLS` · `REFERENCES` · `DEPENDS_ON` ·
`RESOLVES_TO` · `INHERITS_FROM` · `HAS_TYPE` · `EXPOSES` · `CONSUMES` ·
`COMMUNICATES_WITH`

## Span

```python
@dataclass(frozen=True)
class Span:
    start_line: int
    start_col: int
    end_line: int
    end_col: int
```

All four values are **1-based**. (Tree-sitter reports 0-based positions;
adapters convert them when constructing a `Span`.)

## GraphDiff

The result of [`GraphLens.diff`](./graphlens.md#diffother-graphlens---graphdiff).

```python
@dataclass
class GraphDiff:
    added_nodes: list[Node]
    removed_nodes: list[Node]
    changed_nodes: list[tuple[Node, Node]]    # (old, new)
    added_relations: list[Relation]
    removed_relations: list[Relation]

    @property
    def is_empty(self) -> bool: ...           # True when structurally identical
```

## Utility functions

#### `make_node_id(project_name: str, qualified_name: str, kind: str) -> str`
A deterministic 16-character SHA-256 digest of
`project_name::kind::qualified_name`. Same inputs → same id across re-scans and
machines.

#### `make_boundary_id(mechanism: str, key: str) -> str`
A language- and project-agnostic id for a [`BOUNDARY`](../graph-model/boundaries.md)
node, derived only from `(mechanism, key)` so matching server/client ports
collapse on merge.

#### `normalize_http_path(raw: str) -> str`
Normalize an HTTP route or URL to a host- and parameter-agnostic path key
(`http://h/users/42` → `/users/{}`). See
[Boundaries](../graph-model/boundaries.md#http-path-normalization).

#### `normalize_pkg_name(name: str) -> str`
Normalize a package name for comparison: lowercase, hyphens → underscores, strip
extras and version specifiers. Scoped npm names (`@scope/pkg`) are kept as-is,
lowercased. Used by dependency parsers.

## RESOLVER_STATUS_KEY

```python
from graphlens import RESOLVER_STATUS_KEY   # "resolver_status"

graph.metadata[RESOLVER_STATUS_KEY]          # 'ok' | 'degraded' | 'unavailable'
```

The metadata key under which adapters record the
[resolver status](../getting-started/concepts.md#resolver-status). The status
values themselves are the `ResolverStatus` enum (`OK`, `DEGRADED`,
`UNAVAILABLE`); `ResolverStatus.combine(...)` returns the worst of several
statuses when adapter graphs merge.
