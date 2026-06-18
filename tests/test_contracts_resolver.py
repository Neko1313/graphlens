from pathlib import Path

import pytest

from graphlens.contracts import Occurrence, ResolvedRef, SymbolResolver


def test_resolver_is_abstract():
    with pytest.raises(TypeError):
        SymbolResolver()  # type: ignore[abstract]


def test_dtos_are_frozen():
    ref = ResolvedRef(
        full_name="pkg.mod.foo", file_path=Path("/abs/mod.py"),
        line=1, col=5, kind="function", origin="internal",
    )
    occ = Occurrence(
        file_path=Path("/abs/mod.py"), line=3, col=1,
        is_definition=False, access="call",
    )
    with pytest.raises(AttributeError):
        ref.full_name = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        occ.access = "x"  # type: ignore[misc]


def test_concrete_subclass_must_implement_all():
    class Partial(SymbolResolver):
        def prepare(self, project_root, files):  # noqa: ANN001, ANN201
            ...
    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]
