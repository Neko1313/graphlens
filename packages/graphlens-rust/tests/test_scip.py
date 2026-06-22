"""Tests for the dependency-free SCIP wire-format decoder."""

from __future__ import annotations

from graphlens_rust._scip import (
    SCIP_ROLE_DEFINITION,
    ScipOccurrence,
    iter_documents,
)

# ---------------------------------------------------------------------------
# Tiny protobuf encoder — just enough to synthesise SCIP indexes for the tests
# ---------------------------------------------------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _len_field(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _varint_field(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(value)


def _occurrence(
    symbol: str | None,
    roles: int,
    range_ints: list[int],
    *,
    packed: bool = True,
) -> bytes:
    if packed:
        body = _len_field(1, b"".join(_varint(x) for x in range_ints))
    else:
        body = b"".join(_varint_field(1, x) for x in range_ints)
    if symbol is not None:
        body += _len_field(2, symbol.encode())
    if roles:
        body += _varint_field(3, roles)
    # An unknown field (syntax_kind = 5) the decoder must skip.
    body += _varint_field(5, 7)
    return body


def _document(rel_path: str, occurrences: list[bytes]) -> bytes:
    body = _len_field(1, rel_path.encode())
    for occ in occurrences:
        body += _len_field(2, occ)
    return body


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_decodes_packed_range_symbol_and_roles():
    idx = _len_field(
        2,
        _document(
            "src/lib.rs",
            [_occurrence("crate foo", SCIP_ROLE_DEFINITION, [3, 4, 9])],
        ),
    )
    docs = list(iter_documents(idx))
    assert len(docs) == 1
    rel, occs = docs[0]
    assert rel == "src/lib.rs"
    assert occs == [ScipOccurrence("crate foo", SCIP_ROLE_DEFINITION, 3, 4)]


def test_decodes_unpacked_range():
    idx = _len_field(
        2,
        _document(
            "a.rs", [_occurrence("s", 0, [1, 2, 3], packed=False)]
        ),
    )
    (_rel, occs) = next(iter(iter_documents(idx)))
    assert occs[0].start_line == 1
    assert occs[0].start_col == 2
    assert occs[0].roles == 0


def test_multibyte_varint_position():
    # A column past 127 forces a multi-byte varint (exercises the shift loop).
    idx = _len_field(
        2, _document("a.rs", [_occurrence("s", 0, [200, 300, 305])])
    )
    (_rel, occs) = next(iter(iter_documents(idx)))
    assert occs[0].start_line == 200
    assert occs[0].start_col == 300


def test_occurrence_without_range_is_dropped():
    idx = _len_field(2, _document("a.rs", [_occurrence("s", 0, [5])]))
    (_rel, occs) = next(iter(iter_documents(idx)))
    assert occs == []


def test_multiple_documents():
    idx = (
        _len_field(2, _document("a.rs", [_occurrence("x", 0, [0, 0, 1])]))
        + _len_field(2, _document("b.rs", [_occurrence("y", 0, [1, 0, 1])]))
    )
    paths = [rel for rel, _ in iter_documents(idx)]
    assert paths == ["a.rs", "b.rs"]


def test_skips_fixed32_and_fixed64_index_fields():
    # Index-level fields the decoder doesn't consume, in both fixed widths,
    # must be skipped without disturbing document parsing.
    fixed64 = _tag(7, 1) + b"\x00" * 8
    fixed32 = _tag(8, 5) + b"\x00" * 4
    idx = (
        fixed64
        + _len_field(2, _document("a.rs", [_occurrence("x", 0, [0, 0, 1])]))
        + fixed32
    )
    docs = list(iter_documents(idx))
    assert [rel for rel, _ in docs] == ["a.rs"]


def test_range_field_with_unexpected_wire_type_is_ignored():
    # A range field (1) encoded as fixed32 is neither packed nor a varint, so
    # it is skipped; a following packed field-1 still supplies the range.
    occ_body = (
        _tag(1, 5)
        + b"\x00\x00\x00\x00"  # bogus fixed32 for field 1 -> ignored
        + _len_field(1, b"".join(_varint(x) for x in [4, 5, 9]))  # packed
        + _len_field(2, b"sym")
    )
    idx = _len_field(2, _document("a.rs", [occ_body]))
    (_rel, occs) = next(iter(iter_documents(idx)))
    assert occs == [ScipOccurrence("sym", 0, 4, 5)]


def test_empty_index_yields_nothing():
    assert list(iter_documents(b"")) == []
