"""Models, contracts, registry, and utilities for polyglot code analysis."""

from graphlens.contracts import (
    DependencyFileParser,
    DiscoveredProject,
    GraphBackend,
    LanguageAdapter,
    ProjectReader,
    normalize_pkg_name,
)
from graphlens.exceptions import (
    AdapterError,
    AdapterNotFoundError,
    BackendError,
    DiscoveryError,
    DuplicateNodeError,
    GraphLensError,
)
from graphlens.models import (
    GraphLens,
    Node,
    NodeKind,
    Relation,
    RelationKind,
)
from graphlens.registry import AdapterRegistry, adapter_registry

__all__ = [
    "AdapterError",
    "AdapterNotFoundError",
    # registry
    "AdapterRegistry",
    "BackendError",
    # contracts
    "DependencyFileParser",
    "DiscoveredProject",
    "DiscoveryError",
    "DuplicateNodeError",
    "GraphBackend",
    # models
    "GraphLens",
    # exceptions
    "GraphLensError",
    "LanguageAdapter",
    "Node",
    "NodeKind",
    "ProjectReader",
    "Relation",
    "RelationKind",
    "adapter_registry",
    "normalize_pkg_name",
]
