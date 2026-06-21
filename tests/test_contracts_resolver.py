from pathlib import Path

import pytest

from graphlens.contracts import (
    Occurrence,
    Query,
    ResolvedRef,
    SymbolResolver,
)


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
        ref.full_name = "x"  # ty: ignore[invalid-assignment]
    with pytest.raises(AttributeError):
        occ.access = "x"  # ty: ignore[invalid-assignment]


def test_concrete_subclass_must_implement_all():
    class Partial(SymbolResolver):
        def prepare(self, project_root, files):
            ...
    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]


def test_status_defaults_to_ok():
    from graphlens import ResolverStatus

    class Dummy(SymbolResolver):
        def prepare(self, project_root, files): ...
        def definition_at(self, file, line, col): return None
        def infer_type_at(self, file, line, col): return None
        def references_to(self, file, line, col): return []

    assert Dummy().status() is ResolverStatus.OK


def test_resolve_all_default_loops_definition_at():
    """The default resolve_all fans out to definition_at, preserving order."""
    calls: list[Query] = []

    class Dummy(SymbolResolver):
        def prepare(self, project_root, files): ...

        def definition_at(self, file, line, col):
            calls.append((file, line, col))
            return ResolvedRef(
                full_name=f"f{line}", file_path=file, line=line,
                col=col, kind="function", origin="internal",
            )

        def infer_type_at(self, file, line, col): return None
        def references_to(self, file, line, col): return []

    queries: list[Query] = [
        (Path("/a.py"), 1, 2),
        (Path("/b.py"), 3, 4),
    ]
    refs = Dummy().resolve_all(queries)

    assert calls == queries
    assert [r.full_name for r in refs] == ["f1", "f3"]


def test_resolve_all_empty_returns_empty():
    class Dummy(SymbolResolver):
        def prepare(self, project_root, files): ...
        def definition_at(self, file, line, col): return None
        def infer_type_at(self, file, line, col): return None
        def references_to(self, file, line, col): return []

    assert Dummy().resolve_all([]) == []
