---
sidebar_position: 3
---

# Querying the graph

The `GraphLens` query methods are backed by edge indices, so lookups are fast
even on large graphs — you never scan `relations` by hand. This page collects
practical recipes. For the formal method list, see the
[`GraphLens` API reference](../api-reference/graphlens.md).

## Finding a starting node

Most queries take a node **id**. Resolve a name to a node first:

```python
from graphlens import NodeKind

# By name (matches short or qualified name)
candidates = graph.nodes_by_name("save")

# By kind
methods = graph.nodes_by_kind(NodeKind.METHOD)

# By file
in_file = graph.nodes_in_file("src/app/services.py")

node = candidates[0]
```

## Call graph: callers and callees

```python
graph.callers(node.id)   # nodes whose CALLS edge points at node
graph.callees(node.id)   # nodes node's CALLS edges point at
```

Build a reverse-call report — the functions with the most incoming calls:

```python
funcs = graph.nodes_by_kind(NodeKind.FUNCTION) + graph.nodes_by_kind(NodeKind.METHOD)
hot = sorted(funcs, key=lambda n: len(graph.callers(n.id)), reverse=True)
for n in hot[:20]:
    print(len(graph.callers(n.id)), n.qualified_name)
```

Find dead code candidates — functions nobody calls (mind dynamic dispatch and
entry points before deleting anything):

```python
orphans = [n for n in funcs if not graph.callers(n.id)]
```

## References to a value

`REFERENCES` edges capture value uses of variables and attributes (as opposed to
calls). To find everywhere a definition is read:

```python
var = graph.nodes_by_name("DEFAULT_TIMEOUT")[0]
graph.references_to(var.id)
```

## Neighborhoods

`neighbors` returns the distinct nodes within `depth` hops in any direction —
handy for "show me everything around this symbol":

```python
graph.neighbors(node.id, depth=1)
graph.neighbors(node.id, depth=3)
```

## Working with raw edges

When you need the edge itself (to read its `metadata`, or to filter by an exact
kind), use `outgoing`/`incoming`:

```python
from graphlens import RelationKind

# Every type annotation/inference edge leaving a function
for rel in graph.outgoing(node.id, RelationKind.HAS_TYPE):
    target = graph.nodes[rel.target_id]
    print(node.name, "has type", target.qualified_name, rel.metadata)

# Who imports this module
mod = graph.nodes_by_name("app.config")[0]
graph.incoming(mod.id, RelationKind.RESOLVES_TO)
```

## Separating your code from the ecosystem

`IMPORT` and `EXTERNAL_SYMBOL` nodes carry `metadata["origin"]`. Count
third-party usage:

```python
external = graph.nodes_by_kind(NodeKind.EXTERNAL_SYMBOL)
third_party = [n for n in external if n.metadata.get("origin") == "third_party"]
```

## Inheritance

```python
from graphlens import RelationKind

cls = graph.nodes_by_name("OrderService")[0]

# Base classes
bases = [graph.nodes[r.target_id] for r in graph.outgoing(cls.id, RelationKind.INHERITS_FROM)]

# Subclasses
subs = [graph.nodes[r.source_id] for r in graph.incoming(cls.id, RelationKind.INHERITS_FROM)]
```

## Cross-language communication

Once you have run [`graphlens-link`](./cross-language.md), follow
`COMMUNICATES_WITH` edges to see which consumer talks to which provider:

```python
from graphlens import RelationKind

for rel in (r for r in graph.relations if r.kind == RelationKind.COMMUNICATES_WITH):
    consumer = graph.nodes[rel.source_id]
    provider = graph.nodes[rel.target_id]
    print(f"{consumer.qualified_name} → {provider.qualified_name} ({rel.metadata})")
```

## From the command line

The same four operations are available without writing code:

```bash
graphlens query process_order --graph graph.json --op callers
graphlens query process_order --graph graph.json --op callees
graphlens query OrderService.save --graph graph.json --op references
graphlens query OrderService.save --graph graph.json --op neighbors --depth 2
```
