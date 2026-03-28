"""Tests for make_node_id utility."""

from code_graph.utils.ids import make_node_id


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
