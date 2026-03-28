"""Tests for graphlens exception hierarchy."""

from typing import NoReturn

import pytest

from graphlens import (
    AdapterError,
    AdapterNotFoundError,
    BackendError,
    DiscoveryError,
    DuplicateNodeError,
    GraphLensError,
)

EXCEPTION_CLASSES = [
    AdapterNotFoundError,
    AdapterError,
    DuplicateNodeError,
    DiscoveryError,
    BackendError,
]


class TestExceptionHierarchy:
    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES)
    def test_inherits_from_graphlens_error(self, exc_cls) -> None:
        assert issubclass(exc_cls, GraphLensError)

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES)
    def test_inherits_from_exception(self, exc_cls) -> None:
        assert issubclass(exc_cls, Exception)

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES)
    def test_can_be_raised_and_caught_as_itself(self, exc_cls) -> NoReturn:
        with pytest.raises(exc_cls):
            msg = "error message"
            raise exc_cls(msg)

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES)
    def test_can_be_caught_as_graphlens_error(self, exc_cls) -> NoReturn:
        with pytest.raises(GraphLensError):
            msg = "error message"
            raise exc_cls(msg)

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES)
    def test_message_propagates(self, exc_cls) -> None:
        msg = f"specific {exc_cls.__name__} message"
        exc = exc_cls(msg)
        assert str(exc) == msg

    def test_graphlens_error_itself(self) -> NoReturn:
        with pytest.raises(GraphLensError, match="base error"):
            msg = "base error"
            raise GraphLensError(msg)

    def test_adapter_not_found_no_message(self) -> None:
        exc = AdapterNotFoundError()
        assert isinstance(exc, GraphLensError)

    def test_duplicate_node_with_node_id(self) -> None:
        exc = DuplicateNodeError("Node with id 'abc123' already exists")
        assert "abc123" in str(exc)
