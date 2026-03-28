"""Tests for GraphBackend ABC."""

import pytest

from code_graph import CodeGraph, GraphBackend


class ConcreteBackend(GraphBackend):
    def __init__(self) -> None:
        self.stored: list[CodeGraph] = []
        self.cleared = False

    def store(self, graph: CodeGraph) -> None:
        self.stored.append(graph)

    def clear(self) -> None:
        self.stored.clear()
        self.cleared = True


class TestGraphBackendABC:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            GraphBackend()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        backend = ConcreteBackend()
        assert backend is not None

    def test_store_called(self) -> None:
        backend = ConcreteBackend()
        g = CodeGraph()
        backend.store(g)
        assert g in backend.stored

    def test_clear_called(self) -> None:
        backend = ConcreteBackend()
        g = CodeGraph()
        backend.store(g)
        backend.clear()
        assert backend.cleared
        assert backend.stored == []

    def test_multiple_stores(self) -> None:
        backend = ConcreteBackend()
        g1 = CodeGraph()
        g2 = CodeGraph()
        backend.store(g1)
        backend.store(g2)
        assert len(backend.stored) == 2
