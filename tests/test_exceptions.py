"""Tests for code_graph exception hierarchy."""

from typing import NoReturn

import pytest

from code_graph import (
    AdapterError,
    AdapterNotFoundError,
    BackendError,
    CodeGraphError,
    DiscoveryError,
    DuplicateNodeError,
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
    def test_inherits_from_code_graph_error(self, exc_cls) -> None:
        assert issubclass(exc_cls, CodeGraphError)

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES)
    def test_inherits_from_exception(self, exc_cls) -> None:
        assert issubclass(exc_cls, Exception)

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES)
    def test_can_be_raised_and_caught_as_itself(self, exc_cls) -> NoReturn:
        with pytest.raises(exc_cls):
            msg = "error message"
            raise exc_cls(msg)

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES)
    def test_can_be_caught_as_code_graph_error(self, exc_cls) -> NoReturn:
        with pytest.raises(CodeGraphError):
            msg = "error message"
            raise exc_cls(msg)

    @pytest.mark.parametrize("exc_cls", EXCEPTION_CLASSES)
    def test_message_propagates(self, exc_cls) -> None:
        msg = f"specific {exc_cls.__name__} message"
        exc = exc_cls(msg)
        assert str(exc) == msg

    def test_code_graph_error_itself(self) -> NoReturn:
        with pytest.raises(CodeGraphError, match="base error"):
            msg = "base error"
            raise CodeGraphError(msg)

    def test_adapter_not_found_no_message(self) -> None:
        exc = AdapterNotFoundError()
        assert isinstance(exc, CodeGraphError)

    def test_duplicate_node_with_node_id(self) -> None:
        exc = DuplicateNodeError("Node with id 'abc123' already exists")
        assert "abc123" in str(exc)
