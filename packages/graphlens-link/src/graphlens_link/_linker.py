"""Cross-language boundary linker: a pure ``graph -> graph`` transform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from graphlens import NodeKind, Relation, RelationKind

if TYPE_CHECKING:
    from graphlens import GraphLens


@dataclass(frozen=True)
class LinkResult:
    """Summary of a single cross-language link pass."""

    relations_added: int
    boundaries_total: int
    boundaries_linked: int
    """Boundaries that had at least one provider *and* one consumer."""


def _as_float(value: object, default: float = 1.0) -> float:
    """Coerce a metadata value to a float, falling back to ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def link_graph(graph: GraphLens, *, min_confidence: float = 0.0) -> LinkResult:
    """
    Add ``COMMUNICATES_WITH`` edges across cross-language boundaries.

    Adapters emit a shared ``BOUNDARY`` node per contract (id derived purely
    from mechanism + key) with ``EXPOSES`` edges from servers and
    ``CONSUMES`` edges from clients.  This pass pairs, for every boundary,
    each consumer with each provider and adds a directed
    ``consumer -> provider`` edge carrying the boundary's ``mechanism`` and
    the product of the two sides' confidences.

    The graph is mutated in place.  The pass is idempotent: an edge with the
    same ``(source, target, mechanism)`` is never added twice, so it is safe
    to run after re-analyzing part of the graph.  Pairs whose combined
    confidence is below ``min_confidence`` are skipped.
    """
    added = 0
    linked = 0
    boundaries = graph.nodes_by_kind(NodeKind.BOUNDARY)
    existing: set[tuple[str, str, str]] = {
        (r.source_id, r.target_id, str(r.metadata.get("mechanism", "")))
        for r in graph.relations
        if r.kind == RelationKind.COMMUNICATES_WITH
    }
    for boundary in boundaries:
        consumers = graph.incoming(boundary.id, RelationKind.CONSUMES)
        providers = graph.incoming(boundary.id, RelationKind.EXPOSES)
        if not consumers or not providers:
            continue
        linked += 1
        mechanism = str(boundary.metadata.get("mechanism", ""))
        boundary_key = str(boundary.metadata.get("key", ""))
        for consumer in consumers:
            for provider in providers:
                if consumer.source_id == provider.source_id:
                    continue
                confidence = _as_float(
                    consumer.metadata.get("confidence")
                ) * _as_float(provider.metadata.get("confidence"))
                if confidence < min_confidence:
                    continue
                dedupe = (consumer.source_id, provider.source_id, mechanism)
                if dedupe in existing:
                    continue
                existing.add(dedupe)
                graph.add_relation(
                    Relation(
                        source_id=consumer.source_id,
                        target_id=provider.source_id,
                        kind=RelationKind.COMMUNICATES_WITH,
                        metadata={
                            "mechanism": mechanism,
                            "boundary_id": boundary.id,
                            "boundary_key": boundary_key,
                            "confidence": confidence,
                        },
                    )
                )
                added += 1
    return LinkResult(
        relations_added=added,
        boundaries_total=len(boundaries),
        boundaries_linked=linked,
    )
