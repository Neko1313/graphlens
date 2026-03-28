"""Tests for Relation and RelationKind models."""

import dataclasses

import pytest

from code_graph import Relation, RelationKind


class TestRelationKind:
    def test_all_values(self) -> None:
        expected = {
            "CONTAINS", "DECLARES", "IMPORTS", "CALLS",
            "REFERENCES", "DEPENDS_ON", "RESOLVES_TO", "INHERITS_FROM",
        }
        assert {m.name for m in RelationKind} == expected

    def test_string_values(self) -> None:
        assert RelationKind.CONTAINS.value == "contains"
        assert RelationKind.DECLARES.value == "declares"
        assert RelationKind.IMPORTS.value == "imports"
        assert RelationKind.CALLS.value == "calls"
        assert RelationKind.REFERENCES.value == "references"
        assert RelationKind.DEPENDS_ON.value == "depends_on"
        assert RelationKind.RESOLVES_TO.value == "resolves_to"
        assert RelationKind.INHERITS_FROM.value == "inherits_from"

    def test_count(self) -> None:
        assert len(RelationKind) == 8


class TestRelation:
    def test_creation(self) -> None:
        r = Relation(source_id="aaa", target_id="bbb", kind=RelationKind.CONTAINS)
        assert r.source_id == "aaa"
        assert r.target_id == "bbb"
        assert r.kind == RelationKind.CONTAINS
        assert r.metadata == {}

    def test_creation_with_metadata(self) -> None:
        r = Relation(
            source_id="x", target_id="y", kind=RelationKind.CALLS, metadata={"line": 42}
        )
        assert r.metadata == {"line": 42}

    def test_frozen(self) -> None:
        r = Relation(source_id="a", target_id="b", kind=RelationKind.DECLARES)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            r.source_id = "c"  # type: ignore

    def test_equality(self) -> None:
        r1 = Relation(source_id="a", target_id="b", kind=RelationKind.CONTAINS)
        r2 = Relation(source_id="a", target_id="b", kind=RelationKind.CONTAINS)
        assert r1 == r2

    def test_different_kinds_not_equal(self) -> None:
        r1 = Relation(source_id="a", target_id="b", kind=RelationKind.CONTAINS)
        r2 = Relation(source_id="a", target_id="b", kind=RelationKind.DECLARES)
        assert r1 != r2

    def test_metadata_default_is_empty_dict(self) -> None:
        r = Relation(source_id="a", target_id="b", kind=RelationKind.IMPORTS)
        assert isinstance(r.metadata, dict)
