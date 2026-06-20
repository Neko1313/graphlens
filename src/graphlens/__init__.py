"""Models, contracts, registry, and utilities for polyglot code analysis."""

from graphlens.contracts import (
    BoundaryRef,
    DependencyFileParser,
    DiscoveredProject,
    GraphBackend,
    LanguageAdapter,
    ProjectReader,
    normalize_pkg_name,
)
from graphlens.diffing import GraphDiff
from graphlens.exceptions import (
    AdapterError,
    AdapterNotFoundError,
    BackendError,
    DiscoveryError,
    DuplicateNodeError,
    GraphLensError,
    SerializationError,
)
from graphlens.models import (
    GraphLens,
    Node,
    NodeKind,
    Relation,
    RelationKind,
)
from graphlens.registry import AdapterRegistry, adapter_registry
from graphlens.status import RESOLVER_STATUS_KEY, ResolverStatus
from graphlens.utils import make_boundary_id, make_node_id

__all__ = [
    "RESOLVER_STATUS_KEY",
    "AdapterError",
    "AdapterNotFoundError",
    # registry
    "AdapterRegistry",
    "BackendError",
    # contracts
    "BoundaryRef",
    "DependencyFileParser",
    "DiscoveredProject",
    "DiscoveryError",
    "DuplicateNodeError",
    "GraphBackend",
    # diff
    "GraphDiff",
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
    # status
    "ResolverStatus",
    "SerializationError",
    "adapter_registry",
    # utils
    "make_boundary_id",
    "make_node_id",
    "normalize_pkg_name",
]
