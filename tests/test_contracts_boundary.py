"""Tests for the BoundaryRef DTO."""

import dataclasses

import pytest

from graphlens import BoundaryRef


def test_minimal_construction() -> None:
    ref = BoundaryRef(
        mechanism="http",
        role="server",
        key="GET /users/{}",
        line=10,
        col=1,
    )
    assert ref.mechanism == "http"
    assert ref.role == "server"
    assert ref.key == "GET /users/{}"
    assert ref.line == 10
    assert ref.col == 1
    assert ref.confidence == 1.0
    assert dict(ref.detail) == {}


def test_detail_default_is_shared_empty_mapping() -> None:
    a = BoundaryRef(mechanism="queue", role="client", key="t", line=1, col=1)
    b = BoundaryRef(mechanism="queue", role="client", key="t", line=2, col=1)
    # Default detail is an immutable shared empty mapping.
    assert a.detail == b.detail
    assert len(a.detail) == 0


def test_detail_carries_context() -> None:
    ref = BoundaryRef(
        mechanism="http",
        role="client",
        key="POST /orders",
        line=3,
        col=5,
        confidence=0.7,
        detail={"method": "POST", "path": "/orders", "framework": "axios"},
    )
    assert ref.confidence == 0.7
    assert ref.detail["framework"] == "axios"


def test_frozen() -> None:
    ref = BoundaryRef(
        mechanism="grpc", role="server", key="svc/M", line=1, col=1
    )
    with pytest.raises(
        (dataclasses.FrozenInstanceError, AttributeError)
    ):
        ref.key = "other"  # type: ignore[misc]
