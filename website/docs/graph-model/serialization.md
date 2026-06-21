---
sidebar_position: 4
---

# Serialization & diffing

A `GraphLens` round-trips through JSON losslessly, so you can compute a graph
once and reuse it everywhere — a CI artifact, an agent's input, a cache, a
baseline to diff against.

## To and from JSON

```python
# Serialize
text = graph.to_json(indent=2)     # str
data = graph.to_dict()             # JSON-compatible dict

# Deserialize
from graphlens import GraphLens
graph = GraphLens.from_json(text)
graph = GraphLens.from_dict(data)
```

The payload contains the nodes, the relations, and the graph `metadata`
(including the [resolver status](../getting-started/concepts.md#resolver-status)).

## Schema version

Serialized payloads carry a schema version. Loading a payload produced by an
incompatible schema raises `SerializationError`, so you find out immediately
rather than silently misreading old data. Keep producers and consumers on
compatible graphlens versions, or regenerate the graph after an upgrade.

## Diffing

Because node IDs are deterministic, two scans line up by identity and a
structural diff is meaningful:

```python
diff = old_graph.diff(new_graph)
```

`GraphDiff` exposes:

| Field | Type | Meaning |
|---|---|---|
| `added_nodes` | `list[Node]` | present only in the new graph |
| `removed_nodes` | `list[Node]` | present only in the old graph |
| `changed_nodes` | `list[tuple[Node, Node]]` | same id, different content `(old, new)` |
| `added_relations` | `list[Relation]` | edges only in the new graph |
| `removed_relations` | `list[Relation]` | edges only in the old graph |
| `is_empty` | `bool` (property) | `True` when the graphs are structurally identical |

A "what changed in this PR" report is then a few lines:

```python
diff = old.diff(new)
if not diff.is_empty:
    print(f"nodes:     +{len(diff.added_nodes)} -{len(diff.removed_nodes)}")
    print(f"relations: +{len(diff.added_relations)} -{len(diff.removed_relations)}")
```

See [CI integration](../ci-integration/overview.md#caching-the-graph) for using
a cached graph as a diff baseline.

## Merging

`merge` combines another graph into this one (in place):

```python
combined = python_graph
combined.merge(typescript_graph, allow_shared=True)
```

`allow_shared=True` permits identical nodes to coincide rather than raising —
essential for cross-language [`BOUNDARY`](./boundaries.md) nodes, which are
*meant* to be shared across the graphs being merged.
