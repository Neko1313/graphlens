---
sidebar_position: 6
---

# Exceptions

Every graphlens error derives from a single base, `GraphLensError`, so you can
catch the whole family with one `except`.

```python
from graphlens import (
    GraphLensError,
    AdapterError,
    AdapterNotFoundError,
    DuplicateNodeError,
    DiscoveryError,
    BackendError,
    SerializationError,
)
```

## Hierarchy

```
GraphLensError
├── AdapterNotFoundError
├── AdapterError
├── DuplicateNodeError
├── DiscoveryError
├── BackendError
└── SerializationError
```

| Exception | Raised when |
|---|---|
| `GraphLensError` | Base class for all graphlens errors |
| `AdapterNotFoundError` | `adapter_registry.load(name)` finds no adapter for `name` |
| `AdapterError` | An adapter fails during execution — including `analyze(..., strict=True)` when the resolver status is not `ok` |
| `DuplicateNodeError` | `GraphLens.add_node` is given a node whose id already exists |
| `DiscoveryError` | Project discovery fails |
| `BackendError` | A graph backend operation (`store`/`clear`) fails |
| `SerializationError` | Graph (de)serialization fails — e.g. loading a payload with an incompatible schema version |

## Handling them

Catch the base class to handle any graphlens failure uniformly:

```python
from graphlens import GraphLensError, adapter_registry

try:
    adapter = adapter_registry.load("python")()
    graph = adapter.analyze("./my-project", strict=True)
except GraphLensError as exc:
    print(f"graphlens failed: {exc}")
    raise
```

Or be specific when you can recover:

```python
from graphlens import AdapterNotFoundError, adapter_registry

try:
    adapter = adapter_registry.load(lang)()
except AdapterNotFoundError:
    print(f"No adapter for {lang!r}. Available: {adapter_registry.available()}")
```
