---
sidebar_position: 6
---

# Cross-language linking

graphlens can connect a consumer written in one language to a provider written
in another — a TypeScript front end calling a Python FastAPI route, for example.
This works through language-agnostic **boundary** nodes plus the
[`graphlens-link`](../graph-model/boundaries.md) package.

## How it works

1. **Adapters emit boundaries.** While analyzing, each adapter detects the
   interfaces a service exposes or consumes — HTTP routes and clients, message
   queue topics, gRPC methods, Temporal activities — and emits a `BOUNDARY`
   node for each, plus an `EXPOSES` edge (for a provider) or a `CONSUMES` edge
   (for a consumer).

2. **Boundary IDs are language-agnostic.** A boundary's ID comes from
   `make_boundary_id(mechanism, key)` — only the mechanism (e.g. `http`) and a
   normalized key (e.g. `GET /users/{}`). It contains no project or language
   information, so a Python route and a TypeScript `fetch` to the same path
   produce the **same** `BOUNDARY` id.

3. **Merging collapses matching boundaries.** When you merge two graphs with
   `allow_shared=True`, the identical `BOUNDARY` nodes coincide into one.

4. **`link_graph` pairs the two sides.** For each boundary it pairs every
   `CONSUMES` with every `EXPOSES` and adds a `COMMUNICATES_WITH` edge from
   consumer to provider.

## Path normalization

So that `/users/1`, `/users/{user_id}` (FastAPI), `<int:id>` (Flask), and
`:id` (Express) all match, HTTP paths are normalized to a host- and
param-agnostic key with `normalize_http_path`:

- scheme and host are stripped (`http://h/api/x` → `/api/x`)
- query and fragment are dropped
- every path parameter style collapses to `{}`
- concrete numeric ids collapse too (`/users/1` → `/users/{}`)
- the trailing slash is removed (except for the root `/`)

The result is combined with the HTTP method, so the match key looks like
`GET /users/{}`.

## End-to-end example

```python
from graphlens import adapter_registry
from graphlens_link import link_graph

py = adapter_registry.load("python")().analyze(python_project)
ts = adapter_registry.load("typescript")().analyze(typescript_project)

# Merge into one graph; allow_shared lets the BOUNDARY nodes coincide
merged = py
merged.merge(ts, allow_shared=True)

# Add consumer → provider COMMUNICATES_WITH edges
result = link_graph(merged)
print(result.boundaries_linked, "of", result.boundaries_total, "boundaries linked")
print(result.relations_added, "COMMUNICATES_WITH edges added")
```

`link_graph` **mutates the graph in place** and is idempotent — running it twice
will not duplicate edges.

### Filtering by confidence

Each `EXPOSES`/`CONSUMES` edge carries a `confidence` (1.0 for a literal path,
lower for an inferred one). A `COMMUNICATES_WITH` edge's confidence is the
product of the two sides. Drop low-confidence links with `min_confidence`:

```python
result = link_graph(merged, min_confidence=0.5)
```

## Reading the result

```python
from graphlens import RelationKind

for rel in (r for r in merged.relations if r.kind == RelationKind.COMMUNICATES_WITH):
    consumer = merged.nodes[rel.source_id]
    provider = merged.nodes[rel.target_id]
    print(f"{consumer.qualified_name} → {provider.qualified_name}")
    print(f"   {rel.metadata['mechanism']} {rel.metadata['boundary_key']} "
          f"(confidence {rel.metadata['confidence']})")
```

`LinkResult` summarizes the run:

| Field | Meaning |
|---|---|
| `relations_added` | number of `COMMUNICATES_WITH` edges created |
| `boundaries_total` | number of `BOUNDARY` nodes in the graph |
| `boundaries_linked` | how many had at least one consumer paired to a provider |

## A complete walkthrough

The repository's
[`examples/demo_cross_language.py`](https://github.com/Neko1313/graphlens/blob/main/examples/demo_cross_language.py)
builds a tiny FastAPI server and a TypeScript `fetch` client, merges their
graphs, runs `link_graph`, and prints the resulting `COMMUNICATES_WITH` edges.

## See also

- [Graph model → Boundaries](../graph-model/boundaries.md) — the node/edge details.
- [MCP server](./mcp-server.md) — the cross-language agent tools, served by the
  separate [graphlens-mcp](https://github.com/Neko1313/graphlens-mcp) project.
