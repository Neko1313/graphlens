---
sidebar_position: 2
---

# Quick Start

Analyze a real project and explore its graph in a few minutes — first from the
command line, then from Python.

## 1. Install

```bash
pip install "graphlens-cli[python]"
```

## 2. Analyze a project

Point `graphlens analyze` at any Python project root:

```bash
graphlens analyze ./my-project
```

You get a breakdown of the graph — node counts by kind, relation counts by
kind, and the busiest callers:

```text
graphlens · my-project
  nodes:      1240
  relations:  3981
  resolver:   ok

nodes by kind        relations by kind
  FUNCTION    410       CONTAINS    980
  METHOD      265       DECLARES    870
  CLASS        98       CALLS       640
  MODULE       54       REFERENCES  410
  ...                   ...
```

The `resolver: ok` line is important — it tells you the type-aware layer ran
to completion. See [Concepts](./concepts.md#resolver-status) for what the other
values mean.

## 3. Save the graph

Serialize the graph to JSON so you can query it later without re-parsing:

```bash
graphlens analyze ./my-project --output graph.json
```

## 4. Query it

```bash
# Who calls this function?
graphlens query my_function --graph graph.json --op callers

# What does it call?
graphlens query my_function --graph graph.json --op callees

# 2-hop neighborhood around a method
graphlens query MyClass.method --graph graph.json --op neighbors --depth 2
```

## 5. Visualize it

Open an interactive graph in your browser:

```bash
graphlens visualize ./my-project
```

Click any `FUNCTION` or `METHOD` node and press **Show callers** to focus on
just that node and everything that reaches it. See the
[visualization guide](../guides/visualization.md).

## The same thing from Python

Everything the CLI does is available as a library. Load an adapter from the
registry, analyze a project, and query the returned `GraphLens`:

```python
from pathlib import Path
from graphlens import adapter_registry, NodeKind, RESOLVER_STATUS_KEY

# Load and instantiate the Python adapter
adapter = adapter_registry.load("python")()

# Analyze — returns a GraphLens
graph = adapter.analyze(Path("./my-project"))

print(f"Nodes:     {len(graph.nodes)}")
print(f"Relations: {len(graph.relations)}")

# Make sure the resolver actually ran (don't trust a silently degraded graph)
assert graph.metadata[RESOLVER_STATUS_KEY] == "ok"

# Find a function and walk its call graph
fn = graph.nodes_by_name("my_function")[0]
callers = graph.callers(fn.id)          # who calls it
callees = graph.callees(fn.id)          # what it calls
near = graph.neighbors(fn.id, depth=2)  # 2-hop neighbourhood

# Inspect nodes by kind
classes = graph.nodes_by_kind(NodeKind.CLASS)

# Serialize for pipelines / agents, then reload
text = graph.to_json(indent=2)
graph2 = type(graph).from_json(text)
```

## Comparing two scans

`GraphLens.diff` gives you a structural diff between two graphs — useful for
"what changed in this PR" reports:

```python
diff = old_graph.diff(new_graph)
print(diff.added_nodes)        # list[Node]
print(diff.removed_relations)  # list[Relation]
print(diff.is_empty)           # True when the graphs are structurally identical
```

## Next steps

- [Core Concepts](./concepts.md) — the vocabulary behind adapters, resolvers, nodes, and relations.
- [Library API](../guides/library-api.md) — the full `GraphLens` query surface.
- [CLI](../guides/cli.md) — every command and flag.
- [CI Integration](../ci-integration/overview.md) — index your repo on every push.
