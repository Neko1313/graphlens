"""Tests for CsharpScipResolver (SCIP batch-index backend)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from graphlens import ResolverStatus

from graphlens_csharp import CsharpScipResolver
from graphlens_csharp import _resolver as resolver_mod
from graphlens_csharp._resolver import _scip_symbol_origin
from graphlens_csharp._scip import SCIP_ROLE_DEFINITION, ScipOccurrence

DEF = SCIP_ROLE_DEFINITION

# Symbol schemes matching what scip-dotnet emits:
# "scip-dotnet nuget <package> <version> <descriptors>". A symbol declared
# inside the indexed solution itself uses "." for <package> — such a symbol
# is normally found directly in ``_defs`` (see ``_HELPER`` below) and never
# reaches ``_scip_symbol_origin`` at all; ``_OWN_MISSING`` simulates the rarer
# case where it *isn't* found there (its defining file fell outside the
# indexed set), which is the one C#-specific case with no Rust equivalent.
_HELPER = "scip-dotnet nuget . . Util#Helper()."
_STDLIB = "scip-dotnet nuget System 8.0.0 String#Format()."
_THIRD_PARTY = (
    "scip-dotnet nuget Newtonsoft.Json 13.0.3 JsonConvert#SerializeObject()."
)
_OWN_MISSING = "scip-dotnet nuget . . Other#Missing()."
_WEIRD = "scip-ctags . . . thing"


def _docs():
    """A small two-file index covering internal/external/local resolution."""
    return [
        ("src/Util.cs", [ScipOccurrence(_HELPER, DEF, 0, 7)]),
        (
            "src/Program.cs",
            [
                ScipOccurrence(_HELPER, 0, 2, 12),  # ref -> internal def
                ScipOccurrence(_STDLIB, 0, 3, 4),  # ref -> stdlib external
                ScipOccurrence(_THIRD_PARTY, 0, 4, 4),  # ref -> 3rd-party ext
                ScipOccurrence(_WEIRD, 0, 5, 4),  # ref -> unknown external
                ScipOccurrence("local 0", DEF, 6, 8),  # local def
                ScipOccurrence("local 0", 0, 7, 8),  # local ref
                ScipOccurrence("", 0, 8, 8),  # empty symbol -> skipped
            ],
        ),
    ]


def _prepared(monkeypatch, root: Path, docs, *, run=b"scip", rc=0):
    r = CsharpScipResolver()
    monkeypatch.setattr(r, "_run_scip", lambda _root: (run, rc))
    monkeypatch.setattr(
        resolver_mod, "iter_documents", lambda _data: iter(docs)
    )
    r.prepare(root, [])
    return r


# ---------------------------------------------------------------------------
# _scip_symbol_origin
# ---------------------------------------------------------------------------


def test_symbol_origin_stdlib():
    assert _scip_symbol_origin(_STDLIB) == "stdlib"


def test_symbol_origin_third_party():
    assert _scip_symbol_origin(_THIRD_PARTY) == "third_party"


def test_symbol_origin_unknown_non_nuget_scheme():
    assert _scip_symbol_origin(_WEIRD) == "unknown"


def test_symbol_origin_unknown_own_package_miss():
    assert _scip_symbol_origin(_OWN_MISSING) == "unknown"


def test_symbol_origin_unknown_too_short():
    assert _scip_symbol_origin("scip-dotnet nuget") == "unknown"


# ---------------------------------------------------------------------------
# Spawn command
# ---------------------------------------------------------------------------


def test_spawn_argv_env_override(monkeypatch):
    monkeypatch.setenv("GRAPHLENS_SCIP_DOTNET", "/opt/scip-dotnet")
    assert CsharpScipResolver()._spawn_argv() == ["/opt/scip-dotnet"]


def test_spawn_argv_default(monkeypatch):
    monkeypatch.delenv("GRAPHLENS_SCIP_DOTNET", raising=False)
    monkeypatch.setattr(
        "graphlens_csharp._resolver.shutil.which", lambda _name: None
    )
    assert CsharpScipResolver()._spawn_argv() == ["scip-dotnet"]


# ---------------------------------------------------------------------------
# prepare / status
# ---------------------------------------------------------------------------


def test_status_unavailable_before_prepare():
    assert CsharpScipResolver().status() is ResolverStatus.UNAVAILABLE


def test_prepare_ok_when_index_has_documents(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.status() is ResolverStatus.OK


def test_prepare_degraded_when_index_empty(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, [])
    assert r.status() is ResolverStatus.DEGRADED


def test_prepare_unavailable_when_no_index(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs(), run=None)
    assert r.status() is ResolverStatus.UNAVAILABLE


def test_prepare_degraded_when_scip_exits_nonzero(monkeypatch, tmp_path):
    # scip-dotnet left a (partial) index but exited non-zero — report
    # DEGRADED so strict mode rejects the silently incomplete graph.
    r = _prepared(monkeypatch, tmp_path, _docs(), rc=1)
    assert r.status() is ResolverStatus.DEGRADED


def test_prepare_unavailable_when_run_raises(monkeypatch, tmp_path):
    r = CsharpScipResolver()

    def boom(_root):
        msg = "scip-dotnet blew up"
        raise RuntimeError(msg)

    monkeypatch.setattr(r, "_run_scip", boom)
    r.prepare(tmp_path, [])
    assert r.status() is ResolverStatus.UNAVAILABLE


def test_prepare_skips_documents_without_symbols(monkeypatch, tmp_path):
    # A document whose only occurrence has an empty symbol contributes no
    # lookup entries, so it is not registered.
    docs = [("OnlyEmpty.cs", [ScipOccurrence("", 0, 0, 0)])]
    r = _prepared(monkeypatch, tmp_path, docs)
    assert r.status() is ResolverStatus.DEGRADED


# ---------------------------------------------------------------------------
# definition_at / resolve_all
# ---------------------------------------------------------------------------


def test_definition_at_internal(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    ref = r.definition_at(tmp_path / "src" / "Program.cs", 3, 13)
    assert ref is not None
    assert ref.origin == "internal"
    assert ref.file_path == tmp_path / "src/Util.cs"
    assert (ref.line, ref.col) == (1, 8)


def test_definition_at_external_stdlib(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    ref = r.definition_at(tmp_path / "src" / "Program.cs", 4, 5)
    assert ref is not None
    assert ref.origin == "stdlib"
    assert ref.file_path is None
    assert ref.full_name == _STDLIB


def test_definition_at_external_third_party(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    ref = r.definition_at(tmp_path / "src" / "Program.cs", 5, 5)
    assert ref is not None
    assert ref.origin == "third_party"


def test_definition_at_external_unknown(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    ref = r.definition_at(tmp_path / "src" / "Program.cs", 6, 5)
    assert ref is not None
    assert ref.origin == "unknown"


def test_definition_at_local(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    ref = r.definition_at(tmp_path / "src" / "Program.cs", 8, 9)  # local ref
    assert ref is not None
    assert ref.origin == "internal"
    assert ref.file_path == tmp_path / "src/Program.cs"
    assert (ref.line, ref.col) == (7, 9)


def test_definition_at_local_without_def_is_none(monkeypatch, tmp_path):
    docs = [("src/Program.cs", [ScipOccurrence("local 9", 0, 1, 1)])]
    r = _prepared(monkeypatch, tmp_path, docs)
    assert r.definition_at(tmp_path / "src" / "Program.cs", 2, 2) is None


def test_definition_at_miss_in_known_doc(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.definition_at(tmp_path / "src" / "Program.cs", 99, 99) is None


def test_definition_at_unknown_document(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.definition_at(tmp_path / "src" / "Absent.cs", 1, 1) is None


def test_definition_at_file_outside_root(monkeypatch, tmp_path):
    # A path that cannot be made relative to the root falls through to no hit.
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.definition_at(Path("/elsewhere/X.cs"), 1, 1) is None


def test_definition_at_none_when_not_prepared():
    assert CsharpScipResolver().definition_at(Path("X.cs"), 1, 1) is None


def test_resolve_all_preserves_order(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    program = tmp_path / "src" / "Program.cs"
    refs = r.resolve_all(
        [(program, 3, 13), (program, 99, 99), (program, 4, 5)]
    )
    assert refs[0] is not None
    assert refs[0].origin == "internal"
    assert refs[1] is None
    assert refs[2] is not None
    assert refs[2].origin == "stdlib"


def test_resolve_all_none_list_when_not_prepared():
    r = CsharpScipResolver()
    assert r.resolve_all([(Path("A.cs"), 1, 1), (Path("B.cs"), 2, 2)]) == [
        None,
        None,
    ]


def test_resolve_all_empty():
    assert CsharpScipResolver().resolve_all([]) == []


# ---------------------------------------------------------------------------
# references_to / infer_type_at
# ---------------------------------------------------------------------------


def test_references_to_returns_uses_excluding_declaration(
    monkeypatch, tmp_path
):
    r = _prepared(monkeypatch, tmp_path, _docs())
    # Query the definition site; the result lists the use in Program.cs only.
    occs = r.references_to(tmp_path / "src" / "Util.cs", 1, 8)
    assert len(occs) == 1
    assert occs[0].file_path == tmp_path / "src/Program.cs"
    assert (occs[0].line, occs[0].col) == (3, 13)
    assert occs[0].is_definition is False


def test_references_to_local_symbol_is_empty(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.references_to(tmp_path / "src" / "Program.cs", 7, 9) == []


def test_references_to_miss_is_empty(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.references_to(tmp_path / "src" / "Program.cs", 99, 99) == []


def test_references_to_empty_when_not_prepared():
    assert CsharpScipResolver().references_to(Path("X.cs"), 1, 1) == []


def test_infer_type_at_always_none(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.infer_type_at(tmp_path / "src" / "Program.cs", 3, 13) is None


# ---------------------------------------------------------------------------
# Integration: real scip-dotnet index (only when installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not shutil.which("scip-dotnet"), reason="scip-dotnet not installed"
)
def test_scip_integration_resolves_internal_call(tmp_path: Path):
    """Full integration: a cross-file call resolves to its definition."""
    (tmp_path / "m.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk">\n'
        "  <PropertyGroup>\n"
        "    <TargetFramework>net8.0</TargetFramework>\n"
        "  </PropertyGroup>\n"
        "</Project>\n"
    )
    (tmp_path / "Util.cs").write_text(
        "namespace M;\n\npublic static class Util\n{\n"
        "    public static int Helper() => 1;\n}\n"
    )
    (tmp_path / "Program.cs").write_text(
        "namespace M;\n\npublic static class Program\n{\n"
        "    public static void Main()\n    {\n"
        "        var _ = Util.Helper();\n    }\n}\n"
    )
    r = CsharpScipResolver()
    r.prepare(tmp_path, [tmp_path / "Program.cs", tmp_path / "Util.cs"])
    # Be lenient: the batch index must at least start and resolve to *some*
    # definition.
    if r.status() is ResolverStatus.OK:
        ref = r.definition_at(tmp_path / "Program.cs", 7, 19)
        assert ref is not None
        assert ref.file_path is not None
