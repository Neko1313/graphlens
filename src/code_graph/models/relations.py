"""Relation (edge) model for the code graph."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class RelationKind(enum.Enum):
    """Discriminator for the kind of directed edge between two nodes."""

    CONTAINS = "contains"
    DECLARES = "declares"
    IMPORTS = "imports"
    CALLS = "calls"
    REFERENCES = "references"
    DEPENDS_ON = "depends_on"
    RESOLVES_TO = "resolves_to"
    INHERITS_FROM = "inherits_from"


@dataclass(frozen=True, slots=True)
class Relation:
    """A directed edge between two nodes, referenced by ID."""

    source_id: str
    target_id: str
    kind: RelationKind
    metadata: dict[str, object] = field(default_factory=dict)
