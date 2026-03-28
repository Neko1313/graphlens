"""Tests for AdapterRegistry."""

from unittest.mock import MagicMock, patch

import pytest
from helpers import StubAdapter

from graphlens import AdapterNotFoundError, AdapterRegistry


def fresh_registry() -> AdapterRegistry:
    """Return a new AdapterRegistry with no pre-registered adapters."""
    return AdapterRegistry()


class TestRegisterAndLoad:
    def test_register_then_load(self) -> None:
        reg = fresh_registry()
        reg.register("stub", StubAdapter)
        cls = reg.load("stub")
        assert cls is StubAdapter

    def test_load_returns_class_not_instance(self) -> None:
        reg = fresh_registry()
        reg.register("stub", StubAdapter)
        cls = reg.load("stub")
        assert callable(cls)
        assert cls() is not None

    def test_register_overwrites_previous(self) -> None:
        reg = fresh_registry()

        class OtherAdapter(StubAdapter):
            def language(self) -> str:
                return "stub"

        reg.register("stub", StubAdapter)
        reg.register("stub", OtherAdapter)
        assert reg.load("stub") is OtherAdapter

    def test_load_not_found_raises(self) -> None:
        reg = fresh_registry()
        with pytest.raises(AdapterNotFoundError, match="nonexistent"):
            reg.load("nonexistent")

    def test_load_not_found_message_contains_language(self) -> None:
        reg = fresh_registry()
        with pytest.raises(AdapterNotFoundError) as exc_info:
            reg.load("cobol")
        assert "cobol" in str(exc_info.value)


class TestEntryPoints:
    def test_load_via_entry_point(self) -> None:
        reg = fresh_registry()
        mock_ep = MagicMock()
        mock_ep.name = "mock_lang"
        mock_ep.load.return_value = StubAdapter

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            cls = reg.load("mock_lang")

        assert cls is StubAdapter

    def test_load_via_entry_point_caches_result(self) -> None:
        reg = fresh_registry()
        mock_ep = MagicMock()
        mock_ep.name = "mock_lang"
        mock_ep.load.return_value = StubAdapter

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            cls1 = reg.load("mock_lang")

        # Second load should use in-memory cache, not entry points again
        cls2 = reg.load("mock_lang")
        assert cls1 is StubAdapter
        assert cls2 is StubAdapter
        mock_ep.load.assert_called_once()

    def test_load_prefers_in_memory_over_entry_points(self) -> None:
        reg = fresh_registry()
        reg.register("stub", StubAdapter)

        mock_ep = MagicMock()
        mock_ep.name = "stub"

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            cls = reg.load("stub")

        assert cls is StubAdapter
        mock_ep.load.assert_not_called()

    def test_load_entry_point_name_mismatch(self) -> None:
        reg = fresh_registry()
        mock_ep = MagicMock()
        mock_ep.name = "other"
        mock_ep.load.return_value = StubAdapter

        with (
            patch("importlib.metadata.entry_points", return_value=[mock_ep]),
            pytest.raises(AdapterNotFoundError),
        ):
            reg.load("missing")


class TestAvailable:
    def test_available_includes_registered(self) -> None:
        reg = fresh_registry()
        reg.register("stub", StubAdapter)
        assert "stub" in reg.available()

    def test_available_includes_entry_points(self) -> None:
        reg = fresh_registry()
        mock_ep = MagicMock()
        mock_ep.name = "dynlang"

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            names = reg.available()

        assert "dynlang" in names

    def test_available_deduplicates(self) -> None:
        reg = fresh_registry()
        reg.register("stub", StubAdapter)
        mock_ep = MagicMock()
        mock_ep.name = "stub"

        with patch("importlib.metadata.entry_points", return_value=[mock_ep]):
            names = reg.available()

        assert names.count("stub") == 1

    def test_available_sorted(self) -> None:
        reg = fresh_registry()
        reg.register("zebra", StubAdapter)
        reg.register("apple", StubAdapter)
        with patch("importlib.metadata.entry_points", return_value=[]):
            names = reg.available()
        assert names == sorted(names)

    def test_available_empty(self) -> None:
        reg = fresh_registry()
        with patch("importlib.metadata.entry_points", return_value=[]):
            assert reg.available() == []

    def test_real_python_adapter_available(self) -> None:
        """The graphlens-python workspace package registers itself."""
        reg = fresh_registry()
        assert "python" in reg.available()
