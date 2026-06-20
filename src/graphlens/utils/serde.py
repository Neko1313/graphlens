"""
(De)serialization helpers for node and relation metadata values.

Metadata is an open ``dict[str, object]``; these helpers convert it to and
from JSON-compatible structures while round-tripping ``Span`` values via a
small type tag so a deserialized graph equals the original.
"""

from __future__ import annotations

from graphlens.utils.span import Span

_SPAN_TAG = "__span__"
_SPAN_LEN = 4


def span_to_list(span: Span) -> list[int]:
    """Return ``span`` as a 4-int list (start_l, start_c, end_l, end_c)."""
    return [span.start_line, span.start_col, span.end_line, span.end_col]


def span_from_list(data: object) -> Span | None:
    """Reconstruct a span from a 4-int list, or None if malformed."""
    if not isinstance(data, list) or len(data) != _SPAN_LEN:
        return None
    nums = [x for x in data if isinstance(x, int)]
    if len(nums) != _SPAN_LEN:
        return None
    return Span(nums[0], nums[1], nums[2], nums[3])


def encode_value(value: object) -> object:
    """Convert a metadata value to a JSON-compatible structure."""
    if isinstance(value, Span):
        return {_SPAN_TAG: span_to_list(value)}
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (str, int, float)):
        return value
    if isinstance(value, dict):
        return {str(k): encode_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_value(v) for v in value]
    return str(value)


def decode_value(value: object) -> object:
    """Reconstruct a metadata value produced by :func:`encode_value`."""
    if isinstance(value, dict):
        if len(value) == 1 and _SPAN_TAG in value:
            span = span_from_list(next(iter(value.values())))
            if span is not None:
                return span
        return {str(k): decode_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_value(v) for v in value]
    return value


def encode_metadata(metadata: dict[str, object]) -> dict[str, object]:
    """Encode a whole metadata dict to JSON-compatible form."""
    return {str(k): encode_value(v) for k, v in metadata.items()}


def decode_metadata(metadata: object) -> dict[str, object]:
    """Decode a metadata mapping produced by :func:`encode_metadata`."""
    if not isinstance(metadata, dict):
        return {}
    return {str(k): decode_value(v) for k, v in metadata.items()}
