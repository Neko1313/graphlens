"""Public contracts (ABCs) for graphlens adapters and backends."""

from graphlens.contracts.adapter import LanguageAdapter
from graphlens.contracts.backend import GraphBackend
from graphlens.contracts.deps import (
    DependencyFileParser,
    normalize_pkg_name,
)
from graphlens.contracts.reader import DiscoveredProject, ProjectReader
from graphlens.contracts.resolver import (
    Occurrence,
    ResolvedRef,
    SymbolResolver,
)

__all__ = [
    "DependencyFileParser",
    "DiscoveredProject",
    "GraphBackend",
    "LanguageAdapter",
    "Occurrence",
    "ProjectReader",
    "ResolvedRef",
    "SymbolResolver",
    "normalize_pkg_name",
]
