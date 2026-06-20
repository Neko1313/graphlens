"""BoundaryRef: language-agnostic descriptor of a cross-language port."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_EMPTY_DETAIL: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class BoundaryRef:
    """
    One *port* on a cross-language boundary discovered in source code.

    A boundary is a contract between services/components that no single
    compiler can resolve — an HTTP route, a gRPC method, a message-queue
    topic, a Temporal activity.  Each side of that contract (a server that
    *exposes* it, a client that *consumes* it) is a port.

    Extractors live in language adapters (they need the language's
    tree-sitter tree), but they all emit this same language-agnostic
    descriptor.  The adapter turns each ref into a ``BOUNDARY`` node
    (id derived purely from ``mechanism`` + ``key`` via ``make_boundary_id``)
    plus an ``EXPOSES`` or ``CONSUMES`` edge from the enclosing function, so
    independently analyzed graphs in different languages line up on merge.

    Coordinates are 1-based and point at the port site (the route decorator,
    the ``fetch`` call, the ``publish`` call) so the adapter can map it to
    the enclosing declaration.
    """

    mechanism: str
    """Boundary family: ``http`` | ``grpc`` | ``queue`` | ``temporal``."""

    role: str
    """``"server"`` (exposes the contract) or ``"client"`` (consumes it)."""

    key: str
    """Normalized, language-agnostic match key (e.g. ``"GET /users/{}"``)."""

    line: int
    col: int

    confidence: float = 1.0
    """How sure the extractor is (1.0 = literal/exact, lower = inferred)."""

    detail: Mapping[str, str] = field(default=_EMPTY_DETAIL)
    """Extra human-readable context (method, path, topic, framework, raw)."""
