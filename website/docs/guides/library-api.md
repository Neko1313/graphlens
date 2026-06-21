---
sidebar_position: 1
---

# Using graphlens in code

This guide covers the everyday Python workflow: load an adapter, analyze a
project, and work with the resulting `GraphLens`. For an exhaustive method list
see the [`GraphLens` API reference](../api-reference/graphlens.md).

## Loading an adapter

The core never imports adapters directly. Resolve them through the registry,
which discovers installed adapters via entry points:

```python
from graphlens import adapter_registry

adapter_registry.available()          # ['python', 'typescript', ...]
adapter = adapter_registry.load("python")()
```

`load()` returns the adapter **class**; call it to get an instance. If no
adapter is registered for the name, it raises `AdapterNotFoundError`.

## Analyzing a project

```python
from pathlib import Path

graph = adapter.analyze(Path("./my-project"))
```

`analyze` collects every source file the adapter owns, parses each one, runs the
type-aware resolver, and returns a populated `GraphLens`. To analyze a specific
subset of files (for incremental updates or custom filtering), pass them
explicitly:

```python
files = [Path("src/app/main.py"), Path("src/app/db.py")]
graph = adapter.analyze(Path("./my-project"), files=files)
```

### Always check the resolver status

```python
from graphlens import RESOLVER_STATUS_KEY

status = graph.metadata[RESOLVER_STATUS_KEY]   # 'ok' | 'degraded' | 'unavailable'
if status != "ok":
    raise SystemExit(f"resolver did not complete: {status}")
```

Or let the adapter raise for you with `strict=True`, which raises
`AdapterError` when the resolver status is not `ok`:

```python
graph = adapter.analyze(Path("./my-project"), strict=True)
```

## Inspecting nodes

A `GraphLens` exposes `nodes` (a `dict[str, Node]` keyed by id) and `relations`
(a `list[Relation]`), plus indexed lookups so you rarely touch them directly:

```python
from graphlens import NodeKind

graph.nodes_by_kind(NodeKind.CLASS)         # all classes
graph.nodes_by_name("UserService")          # short or qualified name match
graph.nodes_in_file("src/app/main.py")      # everything declared in a file
```

Each `Node` is a frozen dataclass:

```python
node = graph.nodes_by_name("UserService")[0]
node.id            # 'a1b2c3d4e5f6a7b8'
node.kind          # NodeKind.CLASS
node.qualified_name  # 'app.services.UserService'
node.name          # 'UserService'
node.file_path     # 'src/app/services.py'
node.span          # Span(start_line=12, start_col=1, end_line=48, end_col=2)
node.metadata      # {...}
```

## Walking the graph

The query methods take a node **id** and return lists of `Node`:

```python
fn = graph.nodes_by_name("process_order")[0]

graph.callers(fn.id)        # functions/methods that call fn
graph.callees(fn.id)        # functions/methods fn calls
graph.references_to(fn.id)  # nodes that REFERENCE fn (variable/attribute use)
graph.neighbors(fn.id, depth=2)   # distinct nodes within 2 hops, any direction
```

For lower-level access to the raw edges, use `outgoing`/`incoming`, optionally
filtered by relation kind:

```python
from graphlens import RelationKind

graph.outgoing(fn.id, RelationKind.CALLS)   # list[Relation] leaving fn
graph.incoming(fn.id, RelationKind.CALLS)   # list[Relation] arriving at fn
```

See the [Querying guide](./querying.md) for recipes built on these primitives.

## Extracting a subgraph

Carve out a focused slice — useful for visualizing or exporting one file or one
feature:

```python
sub = graph.subgraph_for_file("src/app/services.py")
ids = {n.id for n in graph.nodes_by_kind(NodeKind.CLASS)}
classes_only = graph.subgraph(ids)
```

Both return a new `GraphLens` containing the requested nodes and every relation
incident to them.

## Serializing and reloading

The graph round-trips through JSON losslessly, so you can compute it once and
reuse it everywhere (CI artifact, agent input, cache):

```python
text = graph.to_json(indent=2)
graph.to_dict()                       # JSON-compatible dict instead of a string

restored = type(graph).from_json(text)
# or: from graphlens import GraphLens; GraphLens.from_json(text)
```

The serialized payload carries a schema version; loading a payload from an
incompatible schema raises `SerializationError`.

## Diffing two graphs

```python
diff = old_graph.diff(new_graph)

diff.added_nodes        # list[Node] present only in new_graph
diff.removed_nodes      # list[Node] present only in old_graph
diff.changed_nodes      # list[tuple[Node, Node]] of (old, new) with same id
diff.added_relations    # list[Relation]
diff.removed_relations  # list[Relation]
diff.is_empty           # True when the two graphs are structurally identical
```

Because node IDs are deterministic, the diff lines up nodes by identity across
scans rather than by position.

## Merging graphs

Combine graphs from several languages or several sub-projects into one:

```python
combined = python_graph
combined.merge(typescript_graph, allow_shared=True)
```

Pass `allow_shared=True` when the graphs may contain identical nodes that
*should* coincide — most importantly cross-language `BOUNDARY` nodes, which is
the basis for [cross-language linking](./cross-language.md).

## Putting it together

```python
from pathlib import Path
from graphlens import adapter_registry, NodeKind, RESOLVER_STATUS_KEY

adapter = adapter_registry.load("python")()
graph = adapter.analyze(Path("./my-project"))
assert graph.metadata[RESOLVER_STATUS_KEY] == "ok"

# Report the 10 most-called functions
funcs = graph.nodes_by_kind(NodeKind.FUNCTION) + graph.nodes_by_kind(NodeKind.METHOD)
ranked = sorted(funcs, key=lambda n: len(graph.callers(n.id)), reverse=True)
for n in ranked[:10]:
    print(f"{len(graph.callers(n.id)):4d}  {n.qualified_name}")

# Persist for later
Path("graph.json").write_text(graph.to_json(indent=2))
```

## Next steps

- [Querying the graph](./querying.md) — practical recipes.
- [Cross-language linking](./cross-language.md) — connect services across languages.
- [`GraphLens` API reference](../api-reference/graphlens.md) — every method and signature.
