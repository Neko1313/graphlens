"""Node (entity) model for the code graph."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphlens.utils.span import Span


class NodeKind(enum.Enum):
    """Discriminator for the kind of entity a node represents."""

    PROJECT = "project"
    MODULE = "module"
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    PARAMETER = "parameter"
    IMPORT = "import"
    DEPENDENCY = "dependency"
    SYMBOL = "symbol"
    EXTERNAL_SYMBOL = "external_symbol"


@dataclass(frozen=True, slots=True)
class Node:
    """
    A single entity in the code graph.

    Uses a kind discriminator instead of a class-per-entity hierarchy to
    keep the model flat, serialization-friendly, and easy to produce in
    adapter tight loops.
    """

    id: str
    kind: NodeKind
    qualified_name: str
    name: str
    file_path: str | None = None
    span: Span | None = None
    metadata: dict[str, object] = field(default_factory=dict)
