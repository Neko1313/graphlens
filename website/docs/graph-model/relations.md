---
sidebar_position: 2
---

# Relation kinds

A relation is a directed, typed edge between two nodes. It is a **frozen
dataclass**:

```python
from graphlens import Relation, RelationKind

Relation(
    source_id="a1b2c3d4e5f6a7b8",
    target_id="0011223344556677",
    kind=RelationKind.CALLS,
    metadata={},      # edge data: mechanism, confidence, boundary_key, ...
)
```

| Field | Type | Description |
|---|---|---|
| `source_id` | `str` | Id of the source node |
| `target_id` | `str` | Id of the target node |
| `kind` | `RelationKind` | The edge type |
| `metadata` | `dict[str, object]` | Arbitrary edge data |

## The kinds

| Kind | From → To | Meaning |
|---|---|---|
| `CONTAINS` | project → module → file → class | Structural containment |
| `DECLARES` | file → function, class → method | Declaration |
| `IMPORTS` | file → import | An import statement in a file |
| `RESOLVES_TO` | import → module / external symbol | Where an import resolves |
| `CALLS` | function/method → function/method | A resolved call |
| `REFERENCES` | file/function → variable/attribute | A resolved value reference |
| `INHERITS_FROM` | class → class | A resolved base class |
| `HAS_TYPE` | function/param/variable → class / external | A type annotation or inference |
| `DEPENDS_ON` | project → dependency | A declared package dependency |
| `EXPOSES` | provider → boundary | A server exposes a port (e.g. a route handler) |
| `CONSUMES` | consumer → boundary | A client consumes a port (e.g. an HTTP call) |
| `COMMUNICATES_WITH` | consumer → provider | Added by `graphlens-link` from matching `EXPOSES`/`CONSUMES` |

## Resolved vs. structural edges

- **Structural** edges (`CONTAINS`, `DECLARES`, `IMPORTS`, `DEPENDS_ON`) come
  straight from parsing and are always present.
- **Resolved** edges (`CALLS`, `REFERENCES`, `INHERITS_FROM`, `HAS_TYPE`) come
  from the type-aware resolver. Their completeness depends on the
  [resolver status](../getting-started/concepts.md#resolver-status) — on a
  `degraded` or `unavailable` graph some of these will be missing. Check the
  status (or use `--strict`) before relying on them.
- **Cross-language** edges (`EXPOSES`, `CONSUMES`, `COMMUNICATES_WITH`) connect
  services through [boundaries](./boundaries.md).

## Falling back to external symbols

When a resolved edge's target is not a declaration inside the project — a call
into the standard library or a third-party package — the edge points at an
`EXTERNAL_SYMBOL` node instead, which carries `metadata["origin"]`
(`stdlib` / `third_party` / `internal` / `unknown`). This way a resolved edge is
never silently dropped just because its target lives outside your code.

## Reading edges in code

```python
from graphlens import RelationKind

# All CALLS edges leaving a node
graph.outgoing(node.id, RelationKind.CALLS)
# All CALLS edges arriving at a node
graph.incoming(node.id, RelationKind.CALLS)
```

The convenience methods `callers`, `callees`, `references_to`, and `neighbors`
wrap these — see [Querying the graph](../guides/querying.md).
