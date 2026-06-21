---
sidebar_position: 3
---

# Registry

The adapter registry is how the core finds language adapters without importing
them. There is a module-level singleton, `adapter_registry`, plus the
`AdapterRegistry` class behind it.

```python
from graphlens import adapter_registry, AdapterRegistry
```

## Entry-point group

The registry discovers adapters declared under the `graphlens.adapters`
[entry-point group](https://packaging.python.org/en/latest/specifications/entry-points/):

```toml
[project.entry-points."graphlens.adapters"]
python = "graphlens_python:PythonAdapter"
```

## Methods

#### `load(name: str) -> type[LanguageAdapter]`
Return the adapter **class** registered for `name`. In-memory registrations are
checked first, then entry points. Raises
[`AdapterNotFoundError`](./exceptions.md) if no adapter matches. Call the
returned class to get an instance:

```python
adapter = adapter_registry.load("python")()
```

#### `available() -> list[str]`
Return the sorted names of all available adapters — both manually registered and
discovered through entry points.

```python
adapter_registry.available()      # ['python', 'typescript']
```

#### `register(name: str, adapter_cls: type[LanguageAdapter]) -> None`
Register an adapter class in memory under `name`. Useful in tests, or to
override a discovered adapter:

```python
adapter_registry.register("python", MyPythonAdapter)
```

## Typical use

```python
from pathlib import Path
from graphlens import adapter_registry

for name in adapter_registry.available():
    adapter = adapter_registry.load(name)()
    if adapter.can_handle(Path(".")):
        graph = adapter.analyze(Path("."))
        ...
```

This is exactly what the CLI's `--lang auto` does: enumerate available adapters
and keep the ones whose `can_handle()` returns true for the project.
