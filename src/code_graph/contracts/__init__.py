"""Public contracts (ABCs) for code-graph adapters and backends."""

from code_graph.contracts.adapter import LanguageAdapter
from code_graph.contracts.backend import GraphBackend
from code_graph.contracts.deps import (
    DependencyFileParser,
    normalize_pkg_name,
)
from code_graph.contracts.reader import DiscoveredProject, ProjectReader

__all__ = [
    "DependencyFileParser",
    "DiscoveredProject",
    "GraphBackend",
    "LanguageAdapter",
    "ProjectReader",
    "normalize_pkg_name",
]
