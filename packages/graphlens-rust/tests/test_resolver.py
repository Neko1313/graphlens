"""Tests for RustResolver (structure-only, always degrades)."""

from pathlib import Path

from graphlens import ResolverStatus

from graphlens_rust import RustResolver


def test_resolver_degrades_and_reports_unavailable():
    r = RustResolver()
    r.prepare(Path("."), [])
    assert r.definition_at(Path("x.rs"), 1, 1) is None
    assert r.infer_type_at(Path("x.rs"), 1, 1) is None
    assert r.references_to(Path("x.rs"), 1, 1) == []
    assert r.status() is ResolverStatus.UNAVAILABLE
