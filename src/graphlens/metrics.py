"""
Resolution metrics recorded on an analyzed graph's metadata.

The type-aware pass issues one resolver query per use-site occurrence and
turns each result into a CALLS / REFERENCES / HAS_TYPE / INHERITS_FROM edge.
Adapters tally that pass into a :class:`ResolverMetrics` and store its
``as_dict()`` on ``graph.metadata["resolver_metrics"]`` so callers (and the
benchmark harness) can see *how much* the resolver actually resolved — a fast
run that produced almost no edges is no longer indistinguishable from a fast
run that resolved everything.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Metadata key used to record resolution metrics on a graph.
RESOLVER_METRICS_KEY = "resolver_metrics"


@dataclass
class ResolverMetrics:
    """Counters for a single resolution pass (mergeable across roots)."""

    #: Positions handed to the resolver (one per use-site occurrence).
    queries: int = 0
    #: Queries that returned a definition (``ref is not None``).
    resolved: int = 0
    #: Resolved refs bound to an in-graph declaration node.
    internal: int = 0
    #: Resolved refs that fell back to an EXTERNAL_SYMBOL node.
    external: int = 0
    #: Queries that resolved to nothing (``ref is None``).
    unresolved: int = 0
    #: Wall-clock seconds spent inside ``resolver.resolve_all``.
    seconds: float = 0.0

    @property
    def resolved_pct(self) -> float:
        """Share of queries that returned a definition, in percent."""
        return 100.0 * self.resolved / self.queries if self.queries else 0.0

    def merge(self, other: ResolverMetrics) -> None:
        """Fold another pass's counters into this one (in place)."""
        self.queries += other.queries
        self.resolved += other.resolved
        self.internal += other.internal
        self.external += other.external
        self.unresolved += other.unresolved
        self.seconds += other.seconds

    def as_dict(self) -> dict[str, float | int]:
        """Serialize to a plain dict for ``graph.metadata`` storage."""
        return {
            "queries": self.queries,
            "resolved": self.resolved,
            "internal": self.internal,
            "external": self.external,
            "unresolved": self.unresolved,
            "seconds": round(self.seconds, 3),
            "resolved_pct": round(self.resolved_pct, 1),
        }
