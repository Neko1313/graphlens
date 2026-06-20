"""
Resolver status reported on an analyzed graph's metadata.

Adapters store the ``.value`` of one of these on
``graph.metadata["resolver_status"]`` so callers can tell whether the
type-aware layer (CALLS / REFERENCES / HAS_TYPE / INHERITS_FROM) actually
ran, instead of silently treating a structure-only graph as complete.
"""

from __future__ import annotations

import enum

#: Metadata key used to record the resolver status on a graph.
RESOLVER_STATUS_KEY = "resolver_status"


class ResolverStatus(enum.Enum):
    """How completely the type-aware resolver ran during analysis."""

    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"

    @classmethod
    def combine(cls, statuses: list[ResolverStatus]) -> ResolverStatus:
        """Return the worst status in ``statuses`` (for adapter merges)."""
        order = {cls.OK: 0, cls.DEGRADED: 1, cls.UNAVAILABLE: 2}
        if not statuses:
            return cls.OK
        return max(statuses, key=lambda s: order[s])
