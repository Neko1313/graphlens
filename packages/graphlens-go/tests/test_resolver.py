"""Tests for GoResolver (structure-only, always degrades)."""

from pathlib import Path

from graphlens import ResolverStatus

from graphlens_go import GoResolver


def test_resolver_degrades_and_reports_unavailable():
    r = GoResolver()
    r.prepare(Path("."), [])
    assert r.definition_at(Path("x.go"), 1, 1) is None
    assert r.infer_type_at(Path("x.go"), 1, 1) is None
    assert r.references_to(Path("x.go"), 1, 1) == []
    assert r.status() is ResolverStatus.UNAVAILABLE
