"""Adapter registry — discovers and loads language adapters."""

from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING

from code_graph.exceptions import AdapterNotFoundError

if TYPE_CHECKING:
    from code_graph.contracts.adapter import LanguageAdapter


class AdapterRegistry:
    """
    Registry for language adapters.

    Supports two registration mechanisms (in resolution order):
    1. In-memory registration via ``register()`` — for manual setup
       and testing.
    2. Automatic discovery via ``importlib.metadata`` entry points
       under the ``"code_graph.adapters"`` group — for installed
       adapter packages.

    Adapter packages register themselves in their ``pyproject.toml``::

        [project.entry-points."code_graph.adapters"]
        python = "code_graph_python:PythonAdapter"
    """

    ENTRY_POINT_GROUP = "code_graph.adapters"

    def __init__(self) -> None:
        """Initialise the registry with an empty in-memory store."""
        self._adapters: dict[str, type[LanguageAdapter]] = {}

    def register(self, name: str, adapter_cls: type[LanguageAdapter]) -> None:
        """Register an adapter class for the given language name."""
        self._adapters[name] = adapter_cls

    def load(self, name: str) -> type[LanguageAdapter]:
        """
        Return the adapter class for the given language name.

        Checks in-memory registry first, then entry points.
        Raises :exc:`AdapterNotFoundError` if not found.
        """
        if name in self._adapters:
            return self._adapters[name]

        for ep in importlib.metadata.entry_points(
            group=self.ENTRY_POINT_GROUP
        ):
            if ep.name == name:
                adapter_cls = ep.load()
                self._adapters[name] = adapter_cls
                return adapter_cls

        msg = (
            f"No adapter found for language '{name}'. "
            f"Install a code-graph-{name} package or register an adapter"
            " manually."
        )
        raise AdapterNotFoundError(msg)

    def available(self) -> list[str]:
        """Return names of all available adapters (registered + entry pts)."""
        names: set[str] = set(self._adapters)
        for ep in importlib.metadata.entry_points(
            group=self.ENTRY_POINT_GROUP
        ):
            names.add(ep.name)
        return sorted(names)


adapter_registry = AdapterRegistry()
