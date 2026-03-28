"""Models, contracts, registry, and utilities for polyglot code analysis."""

from code_graph.contracts import (
    DependencyFileParser,
    DiscoveredProject,
    GraphBackend,
    LanguageAdapter,
    ProjectReader,
    normalize_pkg_name,
)
from code_graph.exceptions import (
    AdapterError,
    AdapterNotFoundError,
    BackendError,
    CodeGraphError,
    DiscoveryError,
    DuplicateNodeError,
)
from code_graph.models import (
    CodeGraph,
    Node,
    NodeKind,
    Relation,
    RelationKind,
)
from code_graph.registry import AdapterRegistry, adapter_registry

__all__ = [
    "AdapterError",
    "AdapterNotFoundError",
    # registry
    "AdapterRegistry",
    "BackendError",
    # models
    "CodeGraph",
    # exceptions
    "CodeGraphError",
    # contracts
    "DependencyFileParser",
    "DiscoveredProject",
    "DiscoveryError",
    "DuplicateNodeError",
    "GraphBackend",
    "LanguageAdapter",
    "Node",
    "NodeKind",
    "ProjectReader",
    "Relation",
    "RelationKind",
    "adapter_registry",
    "normalize_pkg_name",
]
