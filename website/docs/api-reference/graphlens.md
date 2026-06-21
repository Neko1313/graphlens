---
sidebar_position: 1
---

# GraphLens

The in-memory graph container and query surface. Import it from the top-level
package:

```python
from graphlens import GraphLens
```

## Construction & data

```python
class GraphLens:
    nodes: dict[str, Node]            # id → Node
    relations: list[Relation]
    metadata: dict[str, object]       # includes the resolver status

    def __init__(self) -> None: ...
```

A fresh `GraphLens()` is empty. Adapters populate it; you normally obtain one
from `adapter.analyze(...)` rather than building it by hand.

## Mutation

#### `add_node(node: Node) -> None`
Add a node. Raises [`DuplicateNodeError`](./exceptions.md) if a node with the
same id already exists.

#### `add_relation(relation: Relation) -> None`
Append a relation and invalidate the edge indices.

#### `merge(other: GraphLens, *, allow_shared: bool = False) -> None`
Merge another graph into this one in place. With `allow_shared=True`, identical
nodes are permitted to coincide instead of raising — used for cross-language
merges where [`BOUNDARY`](../graph-model/boundaries.md) nodes are shared.

## Edge access

#### `outgoing(node_id: str, kind: RelationKind | None = None) -> list[Relation]`
Relations whose `source_id` is `node_id`, optionally filtered by `kind`.

#### `incoming(node_id: str, kind: RelationKind | None = None) -> list[Relation]`
Relations whose `target_id` is `node_id`, optionally filtered by `kind`.

## Query

#### `callees(node_id: str) -> list[Node]`
Nodes that `node_id` calls (the targets of its outgoing `CALLS` edges).

#### `callers(node_id: str) -> list[Node]`
Nodes that call `node_id` (the sources of its incoming `CALLS` edges).

#### `references_to(node_id: str) -> list[Node]`
Nodes that reference `node_id` (incoming `REFERENCES`).

#### `neighbors(node_id: str, depth: int = 1) -> list[Node]`
Distinct nodes within `depth` hops of `node_id`, in any direction.

#### `nodes_by_kind(kind: NodeKind) -> list[Node]`
All nodes of the given kind.

#### `nodes_in_file(file_path: str) -> list[Node]`
All nodes declared in `file_path`.

#### `nodes_by_name(name: str) -> list[Node]`
Nodes whose short **or** qualified name equals `name`.

#### `subgraph(node_ids: Iterable[str]) -> GraphLens`
A new graph containing those nodes and every relation incident to them.

#### `subgraph_for_file(file_path: str) -> GraphLens`
A subgraph of every node in `file_path` and their edges.

## Serialization

#### `to_dict() -> dict[str, object]`
Serialize to a JSON-compatible dict.

#### `to_json(*, indent: int | None = None) -> str`
Serialize to a JSON string.

#### `from_dict(data: dict[str, object]) -> GraphLens` *(classmethod)*
Reconstruct from the output of `to_dict`.

#### `from_json(text: str) -> GraphLens` *(classmethod)*
Reconstruct from the output of `to_json`. Raises
[`SerializationError`](./exceptions.md) on an incompatible schema.

#### `diff(other: GraphLens) -> GraphDiff`
Structural diff from this graph (old) to `other` (new). See
[`GraphDiff`](./models.md#graphdiff).

## Example

```python
from graphlens import adapter_registry, NodeKind, RelationKind

graph = adapter_registry.load("python")().analyze("./my-project")

fn = graph.nodes_by_name("process_order")[0]
graph.callers(fn.id)
graph.callees(fn.id)
graph.neighbors(fn.id, depth=2)
graph.outgoing(fn.id, RelationKind.HAS_TYPE)

graph.subgraph_for_file("src/app/services.py").to_json(indent=2)
```
