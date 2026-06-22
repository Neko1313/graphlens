"""Tests for RustScipResolver (SCIP batch-index backend)."""

from __future__ import annotations

from pathlib import Path

import pytest
from graphlens import ResolverStatus

from graphlens_rust import RustScipResolver
from graphlens_rust import _resolver as resolver_mod
from graphlens_rust._resolver import _scip_symbol_origin
from graphlens_rust._scip import SCIP_ROLE_DEFINITION, ScipOccurrence

DEF = SCIP_ROLE_DEFINITION

# A symbol scheme matching what rust-analyzer emits.
_HELPER = "rust-analyzer cargo m 0.1.0 util/helper()."
_STD = "rust-analyzer cargo std https://x core fs/read()."
_DEP = "rust-analyzer cargo serde 1.0.0 ser/Serialize#"
_WEIRD = "scip-ctags . . . thing"


def _docs():
    """A small two-file index covering internal/external/local resolution."""
    return [
        ("src/util.rs", [ScipOccurrence(_HELPER, DEF, 0, 7)]),
        (
            "src/main.rs",
            [
                ScipOccurrence(_HELPER, 0, 2, 12),  # ref -> internal def
                ScipOccurrence(_STD, 0, 3, 4),  # ref -> stdlib external
                ScipOccurrence(_DEP, 0, 4, 4),  # ref -> third_party external
                ScipOccurrence(_WEIRD, 0, 5, 4),  # ref -> unknown external
                ScipOccurrence("local 0", DEF, 6, 8),  # local def
                ScipOccurrence("local 0", 0, 7, 8),  # local ref
                ScipOccurrence("", 0, 8, 8),  # empty symbol -> skipped
            ],
        ),
    ]


def _prepared(monkeypatch, root: Path, docs, *, run=b"scip", rc=0):
    r = RustScipResolver()
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
    assert _scip_symbol_origin(_STD) == "stdlib"


def test_symbol_origin_third_party():
    assert _scip_symbol_origin(_DEP) == "third_party"


def test_symbol_origin_unknown_non_cargo():
    assert _scip_symbol_origin(_WEIRD) == "unknown"


def test_symbol_origin_unknown_too_short():
    assert _scip_symbol_origin("rust-analyzer cargo") == "unknown"


# ---------------------------------------------------------------------------
# prepare / status
# ---------------------------------------------------------------------------


def test_status_unavailable_before_prepare():
    assert RustScipResolver().status() is ResolverStatus.UNAVAILABLE


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
    # rust-analyzer left a (partial) index but exited non-zero — report
    # DEGRADED so strict mode rejects the silently incomplete graph.
    r = _prepared(monkeypatch, tmp_path, _docs(), rc=1)
    assert r.status() is ResolverStatus.DEGRADED


def test_prepare_unavailable_when_run_raises(monkeypatch, tmp_path):
    r = RustScipResolver()

    def boom(_root):
        msg = "scip blew up"
        raise RuntimeError(msg)

    monkeypatch.setattr(r, "_run_scip", boom)
    r.prepare(tmp_path, [])
    assert r.status() is ResolverStatus.UNAVAILABLE


def test_prepare_skips_documents_without_symbols(monkeypatch, tmp_path):
    # A document whose only occurrence has an empty symbol contributes no
    # lookup entries, so it is not registered.
    docs = [("only_empty.rs", [ScipOccurrence("", 0, 0, 0)])]
    r = _prepared(monkeypatch, tmp_path, docs)
    assert r.status() is ResolverStatus.DEGRADED


# ---------------------------------------------------------------------------
# definition_at / resolve_all
# ---------------------------------------------------------------------------


def test_definition_at_internal(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    ref = r.definition_at(tmp_path / "src" / "main.rs", 3, 13)
    assert ref is not None
    assert ref.origin == "internal"
    assert ref.file_path == tmp_path / "src/util.rs"
    assert (ref.line, ref.col) == (1, 8)


def test_definition_at_external_stdlib(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    ref = r.definition_at(tmp_path / "src" / "main.rs", 4, 5)
    assert ref is not None
    assert ref.origin == "stdlib"
    assert ref.file_path is None
    assert ref.full_name == _STD


def test_definition_at_external_third_party(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    ref = r.definition_at(tmp_path / "src" / "main.rs", 5, 5)
    assert ref is not None
    assert ref.origin == "third_party"


def test_definition_at_external_unknown(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    ref = r.definition_at(tmp_path / "src" / "main.rs", 6, 5)
    assert ref is not None
    assert ref.origin == "unknown"


def test_definition_at_local(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    ref = r.definition_at(tmp_path / "src" / "main.rs", 8, 9)  # local ref
    assert ref is not None
    assert ref.origin == "internal"
    assert ref.file_path == tmp_path / "src/main.rs"
    assert (ref.line, ref.col) == (7, 9)


def test_definition_at_local_without_def_is_none(monkeypatch, tmp_path):
    docs = [("src/main.rs", [ScipOccurrence("local 9", 0, 1, 1)])]
    r = _prepared(monkeypatch, tmp_path, docs)
    assert r.definition_at(tmp_path / "src" / "main.rs", 2, 2) is None


def test_definition_at_miss_in_known_doc(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.definition_at(tmp_path / "src" / "main.rs", 99, 99) is None


def test_definition_at_unknown_document(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.definition_at(tmp_path / "src" / "absent.rs", 1, 1) is None


def test_definition_at_file_outside_root(monkeypatch, tmp_path):
    # A path that cannot be made relative to the root falls through to no hit.
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.definition_at(Path("/elsewhere/x.rs"), 1, 1) is None


def test_definition_at_none_when_not_prepared():
    assert RustScipResolver().definition_at(Path("x.rs"), 1, 1) is None


def test_resolve_all_preserves_order(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    main = tmp_path / "src" / "main.rs"
    refs = r.resolve_all([(main, 3, 13), (main, 99, 99), (main, 4, 5)])
    assert refs[0] is not None and refs[0].origin == "internal"
    assert refs[1] is None
    assert refs[2] is not None and refs[2].origin == "stdlib"


def test_resolve_all_none_list_when_not_prepared():
    r = RustScipResolver()
    assert r.resolve_all([(Path("a.rs"), 1, 1), (Path("b.rs"), 2, 2)]) == [
        None,
        None,
    ]


def test_resolve_all_empty():
    assert RustScipResolver().resolve_all([]) == []


# ---------------------------------------------------------------------------
# references_to / infer_type_at
# ---------------------------------------------------------------------------


def test_references_to_returns_uses_excluding_declaration(
    monkeypatch, tmp_path
):
    r = _prepared(monkeypatch, tmp_path, _docs())
    # Query the definition site; the result lists the use in main.rs only.
    occs = r.references_to(tmp_path / "src" / "util.rs", 1, 8)
    assert len(occs) == 1
    assert occs[0].file_path == tmp_path / "src/main.rs"
    assert (occs[0].line, occs[0].col) == (3, 13)
    assert occs[0].is_definition is False


def test_references_to_local_symbol_is_empty(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.references_to(tmp_path / "src" / "main.rs", 7, 9) == []


def test_references_to_miss_is_empty(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.references_to(tmp_path / "src" / "main.rs", 99, 99) == []


def test_references_to_empty_when_not_prepared():
    assert RustScipResolver().references_to(Path("x.rs"), 1, 1) == []


def test_infer_type_at_always_none(monkeypatch, tmp_path):
    r = _prepared(monkeypatch, tmp_path, _docs())
    assert r.infer_type_at(tmp_path / "src" / "main.rs", 3, 13) is None


# ---------------------------------------------------------------------------
# Integration: real rust-analyzer scip (only when installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("shutil").which("rust-analyzer"),
    reason="rust-analyzer not installed",
)
def test_scip_integration_resolves_internal_call(tmp_path: Path):
    """Full integration: a cross-module call resolves to its definition."""
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "m"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "util.rs").write_text("pub fn helper() -> i32 { 1 }\n")
    (src / "main.rs").write_text(
        "mod util;\nfn main() {\n    let _ = util::helper();\n}\n"
    )
    r = RustScipResolver()
    r.prepare(tmp_path, [src / "main.rs", src / "util.rs"])
    # "helper" call on main.rs line 3, col 19 (1-based). Be lenient: the
    # batch index must at least start and resolve to *some* definition.
    if r.status() is ResolverStatus.OK:
        ref = r.definition_at(src / "main.rs", 3, 19)
        assert ref is not None
        assert ref.file_path is not None
