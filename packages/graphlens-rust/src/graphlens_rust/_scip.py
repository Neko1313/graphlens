"""
Dependency-free decoder for the subset of SCIP that the resolver consumes.

SCIP (https://github.com/sourcegraph/scip) is the protobuf index emitted by
``rust-analyzer scip``. Driving that batch index instead of an interactive
``rust-analyzer`` LSP server is what keeps memory bounded — the server holds
the whole workspace's analysis state resident, while the index is written once
and read back statically.

We only need three things per occurrence — its ``symbol``, its ``symbol_roles``
bitfield, and the *start* of its source ``range`` — so rather than pull in the
protobuf runtime we decode the wire format directly. The field numbers below
come from ``scip.proto``:

* ``Index.documents`` = 2
* ``Document.relative_path`` = 1, ``Document.occurrences`` = 2
* ``Occurrence.range`` = 1 (packed ``int32``), ``Occurrence.symbol`` = 2,
  ``Occurrence.symbol_roles`` = 3

Coordinates in SCIP are 0-based ``[start_line, start_char, ...]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

#: ``Occurrence.symbol_roles`` bit set on a definition site.
SCIP_ROLE_DEFINITION = 0x1

# Protobuf wire types we handle.
_WIRE_VARINT = 0
_WIRE_I64 = 1
_WIRE_LEN = 2
_WIRE_I32 = 5

# Protobuf field numbers from scip.proto.
_INDEX_DOCUMENTS = 2
_DOC_RELATIVE_PATH = 1
_DOC_OCCURRENCES = 2
_OCC_RANGE = 1
_OCC_SYMBOL = 2
_OCC_ROLES = 3

# An occurrence range must carry at least [start_line, start_col].
_RANGE_MIN_LEN = 2


@dataclass(frozen=True, slots=True)
class ScipOccurrence:
    """One occurrence of a symbol in a document. Coordinates are 0-based."""

    symbol: str
    roles: int
    start_line: int
    start_col: int


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    """Decode a base-128 varint at *i*; return ``(value, next_index)``."""
    shift = 0
    result = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7


def _iter_fields(buf: bytes) -> Iterator[tuple[int, int, object]]:
    """
    Yield ``(field_number, wire_type, value)`` for every field in *buf*.

    ``value`` is an ``int`` for varint/fixed fields and ``bytes`` for
    length-delimited ones. Unknown-but-well-formed fields are still yielded so
    callers skip what they do not need.
    """
    i = 0
    n = len(buf)
    while i < n:
        tag, i = _read_varint(buf, i)
        field_number = tag >> 3
        wire_type = tag & 0x7
        if wire_type == _WIRE_VARINT:
            value, i = _read_varint(buf, i)
            yield field_number, wire_type, value
        elif wire_type == _WIRE_LEN:
            length, i = _read_varint(buf, i)
            yield field_number, wire_type, buf[i : i + length]
            i += length
        elif wire_type == _WIRE_I64:
            yield field_number, wire_type, buf[i : i + 8]
            i += 8
        elif wire_type == _WIRE_I32:
            yield field_number, wire_type, buf[i : i + 4]
            i += 4
        else:  # pragma: no cover - groups (3/4) are not used by SCIP
            msg = f"unsupported wire type {wire_type}"
            raise ValueError(msg)


def _packed_varints(buf: bytes) -> list[int]:
    """Decode a packed repeated varint field into a list of ints."""
    out: list[int] = []
    i = 0
    n = len(buf)
    while i < n:
        value, i = _read_varint(buf, i)
        out.append(value)
    return out


def _parse_occurrence(buf: bytes) -> ScipOccurrence | None:
    """Parse one ``Occurrence`` message; None if it carries no range."""
    symbol = ""
    roles = 0
    range_ints: list[int] = []
    for field_number, wire_type, value in _iter_fields(buf):
        if field_number == _OCC_RANGE:
            if wire_type == _WIRE_LEN:  # packed (the common case)
                range_ints.extend(_packed_varints(cast("bytes", value)))
            elif wire_type == _WIRE_VARINT:  # unpacked fallback
                range_ints.append(cast("int", value))
        elif field_number == _OCC_SYMBOL and wire_type == _WIRE_LEN:
            symbol = cast("bytes", value).decode("utf-8", "replace")
        elif field_number == _OCC_ROLES and wire_type == _WIRE_VARINT:
            roles = cast("int", value)
    if len(range_ints) < _RANGE_MIN_LEN:
        return None
    return ScipOccurrence(symbol, roles, range_ints[0], range_ints[1])


def _parse_document(buf: bytes) -> tuple[str, list[ScipOccurrence]]:
    """Parse one ``Document`` into ``(relative_path, occurrences)``."""
    rel_path = ""
    occurrences: list[ScipOccurrence] = []
    for field_number, wire_type, value in _iter_fields(buf):
        if field_number == _DOC_RELATIVE_PATH and wire_type == _WIRE_LEN:
            rel_path = cast("bytes", value).decode("utf-8", "replace")
        elif field_number == _DOC_OCCURRENCES and wire_type == _WIRE_LEN:
            occ = _parse_occurrence(cast("bytes", value))
            if occ is not None:
                occurrences.append(occ)
    return rel_path, occurrences


def iter_documents(
    data: bytes,
) -> Iterator[tuple[str, list[ScipOccurrence]]]:
    """
    Yield ``(relative_path, occurrences)`` for every document in a SCIP index.

    Documents are streamed one at a time so the caller can fold each into its
    lookup tables and drop the intermediate list, keeping peak memory low.
    """
    for field_number, wire_type, value in _iter_fields(data):
        if field_number == _INDEX_DOCUMENTS and wire_type == _WIRE_LEN:
            yield _parse_document(cast("bytes", value))
