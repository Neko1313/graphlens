"""Tests for GraphBackend ABC."""

import pytest

from graphlens import GraphBackend, GraphLens


class ConcreteBackend(GraphBackend):
    def __init__(self) -> None:
        self.stored: list[GraphLens] = []
        self.cleared = False

    def store(self, graph: GraphLens) -> None:
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
        g = GraphLens()
        backend.store(g)
        assert g in backend.stored

    def test_clear_called(self) -> None:
        backend = ConcreteBackend()
        g = GraphLens()
        backend.store(g)
        backend.clear()
        assert backend.cleared
        assert backend.stored == []

    def test_multiple_stores(self) -> None:
        backend = ConcreteBackend()
        g1 = GraphLens()
        g2 = GraphLens()
        backend.store(g1)
        backend.store(g2)
        assert len(backend.stored) == 2
