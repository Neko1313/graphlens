"""Tests for metadata (de)serialization helpers."""

import json

from graphlens.utils.serde import (
    decode_metadata,
    decode_value,
    encode_metadata,
    encode_value,
    span_from_list,
    span_to_list,
)
from graphlens.utils.span import Span


def test_span_round_trip() -> None:
    s = Span(1, 2, 3, 4)
    assert span_from_list(span_to_list(s)) == s


def test_span_from_list_malformed() -> None:
    assert span_from_list("nope") is None
    assert span_from_list([1, 2, 3]) is None
    assert span_from_list([1, 2, 3, "x"]) is None


def test_encode_decode_nested_structures() -> None:
    value = {"a": [1, {"b": Span(1, 1, 2, 2)}], "c": ("t", 2)}
    encoded = encode_value(value)
    json.dumps(encoded)  # must be JSON-serializable
    decoded = decode_value(encoded)
    assert decoded["a"][1]["b"] == Span(1, 1, 2, 2)
    assert decoded["c"] == ["t", 2]  # tuple normalizes to list


def test_encode_value_falls_back_to_str() -> None:
    class Weird:
        def __str__(self) -> str:
            return "weird"

    assert encode_value(Weird()) == "weird"


def test_decode_value_scalar_passthrough() -> None:
    assert decode_value(5) == 5
    assert decode_value("x") == "x"


def test_decode_value_malformed_span_tag_stays_dict() -> None:
    assert decode_value({"__span__": [1, 2]}) == {"__span__": [1, 2]}


def test_decode_metadata_non_dict_returns_empty() -> None:
    assert decode_metadata(None) == {}
    assert decode_metadata("x") == {}


def test_encode_metadata_tags_span() -> None:
    assert encode_metadata({"a": Span(1, 1, 1, 1)}) == {
        "a": {"__span__": [1, 1, 1, 1]}
    }
