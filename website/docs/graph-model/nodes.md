---
sidebar_position: 1
---

# Node kinds

A node is a typed entity in the graph. Every node is a **frozen dataclass**:

```python
from graphlens import Node, NodeKind

Node(
    id="a1b2c3d4e5f6a7b8",
    kind=NodeKind.FUNCTION,
    qualified_name="app.services.process_order",
    name="process_order",
    file_path="src/app/services.py",   # optional
    span=Span(12, 1, 30, 2),           # optional, 1-based
    metadata={},                       # free-form
)
```

| Field | Type | Description |
|---|---|---|
| `id` | `str` | Deterministic 16-char id (see below) |
| `kind` | `NodeKind` | The discriminator |
| `qualified_name` | `str` | Fully qualified name (`module.Class.method`) |
| `name` | `str` | Short name (`method`) |
| `file_path` | `str \| None` | Source file, when applicable |
| `span` | `Span \| None` | 1-based source range |
| `metadata` | `dict[str, object]` | Arbitrary extra data (e.g. `origin`) |

## The kinds

| Kind | Description |
|---|---|
| `PROJECT` | Root project node |
| `MODULE` | A module (directory or file), language-dependent |
| `FILE` | A source file |
| `CLASS` | A class declaration |
| `FUNCTION` | A top-level function |
| `METHOD` | A method inside a class |
| `PARAMETER` | A function/method parameter |
| `VARIABLE` | A module-level or local variable |
| `ATTRIBUTE` | A class attribute |
| `TYPE_ALIAS` | A type-alias declaration |
| `IMPORT` | An import statement |
| `DEPENDENCY` | A declared package dependency |
| `EXTERNAL_SYMBOL` | A symbol outside the project (stdlib, third-party, or unknown) |
| `BOUNDARY` | A cross-language interface port (see [Boundaries](./boundaries.md)) |

## Deterministic IDs

IDs are a SHA-256 hash of `project_name::kind::qualified_name`, truncated to 16
hex characters:

```python
from graphlens import make_node_id
make_node_id("my-project", "app.services.process_order", "FUNCTION")
```

Because the ID depends only on identity — not on file position — re-scanning a
project yields the same IDs. That is what makes
[`diff`](./serialization.md#diffing) and incremental updates work.

## The `origin` metadata

`IMPORT` and `EXTERNAL_SYMBOL` nodes always carry `metadata["origin"]`:

| Value | Meaning |
|---|---|
| `stdlib` | language standard library |
| `internal` | a module declared in the same project (a fallback when the `MODULE` node is not yet in the graph) |
| `third_party` | a package listed in a dependency manifest |
| `unknown` | none of the above |

This is the line between "our code" and "the ecosystem". See
[querying recipes](../guides/querying.md#separating-your-code-from-the-ecosystem).

## Typical hierarchy

```
PROJECT
  └─(CONTAINS)─ MODULE
                  └─(CONTAINS)─ FILE
                                  └─(DECLARES)─ CLASS
                                                  └─(DECLARES)─ METHOD
                                  └─(DECLARES)─ FUNCTION
                                  └─(DECLARES)─ VARIABLE / ATTRIBUTE / TYPE_ALIAS
                                  └─(DECLARES)─ IMPORT ─(RESOLVES_TO)─ MODULE (internal)
                                                        └─(RESOLVES_TO)─ EXTERNAL_SYMBOL (stdlib/third_party/unknown)
```

See [Relation kinds](./relations.md) for the edges.
