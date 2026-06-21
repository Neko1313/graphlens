---
sidebar_position: 3
---

# Cross-language boundaries

A **boundary** is a language-agnostic description of an interface a service
exposes or consumes — an HTTP route, a queue topic, a gRPC method, a Temporal
activity. Boundaries are the mechanism that lets graphlens connect a consumer in
one language to a provider in another.

## The `BOUNDARY` node

Adapters emit a `BOUNDARY` node for each port they detect, plus an edge:

- `EXPOSES` — from a **provider** to the boundary (e.g. a FastAPI route handler)
- `CONSUMES` — from a **consumer** to the boundary (e.g. a `fetch` call)

## Language-agnostic IDs

A boundary's ID comes from `make_boundary_id(mechanism, key)` and contains no
project or language information:

```python
from graphlens import make_boundary_id
make_boundary_id("http", "GET /users/{}")
```

Because the ID depends only on `(mechanism, key)`, a Python route and a
TypeScript client targeting the same endpoint produce the **same** `BOUNDARY`
id. When two graphs are merged with `allow_shared=True`, those identical nodes
collapse into one — which is what allows linking across languages.

## The `BoundaryRef` descriptor

While analyzing, an adapter describes each port with a `BoundaryRef` before it
becomes a node:

| Field | Meaning |
|---|---|
| `mechanism` | boundary family: `http` \| `grpc` \| `queue` \| `temporal` |
| `role` | `server` (exposes) or `client` (consumes) |
| `key` | normalized match key, e.g. `GET /users/{}` |
| `line`, `col` | 1-based position of the port site |
| `confidence` | extractor certainty (1.0 = literal/exact, lower = inferred) |
| `detail` | human-readable context (method, path, topic, framework, raw) |

## HTTP path normalization

So that different path styles match, HTTP keys are normalized with
`normalize_http_path`:

```python
from graphlens import normalize_http_path
normalize_http_path("http://api.example.com/users/42?x=1")   # -> "/users/{}"
```

- scheme and host are stripped
- query and fragment are dropped
- every path-parameter style collapses to `{}` — `{id}` (FastAPI),
  `<int:id>` (Flask), `:id` (Express)
- concrete numeric ids collapse too (`/users/1` → `/users/{}`)
- the trailing slash is removed, except for the root `/`

The normalized path is combined with the HTTP method into the boundary key, e.g.
`GET /users/{}`.

## Linking the two sides

The `BOUNDARY` node and its `EXPOSES`/`CONSUMES` edges are only half the story.
The [`graphlens-link`](../guides/cross-language.md) package walks each boundary,
pairs consumers with providers, and adds `COMMUNICATES_WITH` edges:

```python
from graphlens_link import link_graph

merged = python_graph
merged.merge(typescript_graph, allow_shared=True)
result = link_graph(merged)     # adds COMMUNICATES_WITH edges
```

A `COMMUNICATES_WITH` edge's confidence is the product of the consumer's and
provider's confidences; `link_graph(min_confidence=...)` filters weak links.

See the [cross-language guide](../guides/cross-language.md) for the full
workflow and a runnable example.
