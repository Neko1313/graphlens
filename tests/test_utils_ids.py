"""Tests for make_node_id utility."""

from graphlens.utils.ids import make_boundary_id, make_node_id


class TestMakeNodeId:
    def test_returns_string(self) -> None:
        result = make_node_id("proj", "proj.mod", "module")
        assert isinstance(result, str)

    def test_length_is_16(self) -> None:
        result = make_node_id("proj", "proj.mod", "module")
        assert len(result) == 16

    def test_is_hex(self) -> None:
        result = make_node_id("proj", "proj.mod", "module")
        int(result, 16)  # raises if not valid hex

    def test_deterministic(self) -> None:
        r1 = make_node_id("proj", "proj.mod", "module")
        r2 = make_node_id("proj", "proj.mod", "module")
        assert r1 == r2

    def test_different_project_differs(self) -> None:
        r1 = make_node_id("proj_a", "mod", "module")
        r2 = make_node_id("proj_b", "mod", "module")
        assert r1 != r2

    def test_different_qname_differs(self) -> None:
        r1 = make_node_id("proj", "mod.a", "module")
        r2 = make_node_id("proj", "mod.b", "module")
        assert r1 != r2

    def test_different_kind_differs(self) -> None:
        r1 = make_node_id("proj", "mod", "module")
        r2 = make_node_id("proj", "mod", "class")
        assert r1 != r2

    def test_empty_strings(self) -> None:
        result = make_node_id("", "", "")
        assert len(result) == 16

    def test_unicode_input(self) -> None:
        result = make_node_id("мой_проект", "модуль.класс", "class")
        assert len(result) == 16


class TestMakeBoundaryId:
    def test_length_is_16(self) -> None:
        assert len(make_boundary_id("http", "GET /users")) == 16

    def test_is_hex(self) -> None:
        int(make_boundary_id("http", "GET /users"), 16)

    def test_deterministic(self) -> None:
        r1 = make_boundary_id("http", "GET /users/{}")
        r2 = make_boundary_id("http", "GET /users/{}")
        assert r1 == r2

    def test_project_and_language_agnostic(self) -> None:
        """Same mechanism+key yields one ID regardless of who emits it."""
        server_side = make_boundary_id("grpc", "user.v1.Users/Get")
        client_side = make_boundary_id("grpc", "user.v1.Users/Get")
        assert server_side == client_side

    def test_different_mechanism_differs(self) -> None:
        assert make_boundary_id("http", "orders") != make_boundary_id(
            "queue", "orders"
        )

    def test_different_key_differs(self) -> None:
        assert make_boundary_id("http", "GET /a") != make_boundary_id(
            "http", "GET /b"
        )

    def test_distinct_from_node_id(self) -> None:
        """A boundary ID never collides with a normal node ID space."""
        assert make_boundary_id("http", "x") != make_node_id(
            "proj", "x", "boundary"
        )
