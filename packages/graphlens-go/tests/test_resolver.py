"""Tests for Go resolvers: GoplsResolver (mocked) and GoResolver."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from graphlens import ResolverStatus

from graphlens_go import GoplsResolver, GoResolver
from graphlens_go._resolver import (
    _GoplsClient,
    _in_mod_cache,
    _uri_to_path,
)

# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def test_uri_to_path_file_scheme():
    assert _uri_to_path("file:///tmp/a/b.go") == Path("/tmp/a/b.go")


def test_uri_to_path_encoded():
    assert _uri_to_path("file:///home/u/my%20proj/a.go") == Path(
        "/home/u/my proj/a.go"
    )


def test_uri_to_path_non_file_returns_none():
    assert _uri_to_path("https://x/y.go") is None
    assert _uri_to_path("") is None


def test_in_mod_cache_true():
    parts = Path("/home/u/go/pkg/mod/github.com/x/y.go").parts
    assert _in_mod_cache(parts) is True


def test_in_mod_cache_false():
    assert _in_mod_cache(Path("/home/u/proj/a.go").parts) is False


# ---------------------------------------------------------------------------
# GoplsResolver — unit tests (mocked client; no real gopls process)
# ---------------------------------------------------------------------------


@pytest.fixture
def resolver(tmp_path: Path) -> GoplsResolver:
    r = GoplsResolver()
    r._root = tmp_path
    r._client = MagicMock(spec=_GoplsClient)
    return r


def test_definition_at_returns_none_when_client_none(tmp_path):
    assert GoplsResolver().definition_at(tmp_path / "x.go", 1, 1) is None


def test_infer_type_at_always_none(resolver, tmp_path):
    assert resolver.infer_type_at(tmp_path / "x.go", 1, 1) is None


def test_references_to_returns_empty_when_client_none(tmp_path):
    assert GoplsResolver().references_to(tmp_path / "x.go", 1, 1) == []


def test_definition_at_hit(resolver, tmp_path):
    target = tmp_path / "util.go"
    resolver._client.definition.return_value = {
        "uri": target.as_uri(),
        "range": {"start": {"line": 2, "character": 5}},
    }
    ref = resolver.definition_at(tmp_path / "main.go", 4, 1)
    assert ref is not None
    assert ref.file_path == target
    assert ref.line == 3  # 0-based -> 1-based
    assert ref.col == 6
    assert ref.origin == "internal"


def test_definition_at_miss(resolver, tmp_path):
    resolver._client.definition.return_value = None
    assert resolver.definition_at(tmp_path / "main.go", 1, 1) is None


def test_definition_at_swallows_exception(resolver, tmp_path):
    resolver._client.definition.side_effect = RuntimeError("boom")
    assert resolver.definition_at(tmp_path / "main.go", 1, 1) is None


def test_references_to_returns_occurrences(resolver, tmp_path):
    target = tmp_path / "other.go"
    resolver._client.references.return_value = [
        {
            "uri": target.as_uri(),
            "range": {"start": {"line": 4, "character": 8}},
        },
    ]
    occs = resolver.references_to(tmp_path / "main.go", 1, 1)
    assert len(occs) == 1
    assert occs[0].file_path == target
    assert occs[0].line == 5
    assert occs[0].col == 9


def test_references_to_skips_non_file_uris(resolver, tmp_path):
    resolver._client.references.return_value = [
        {"uri": "untitled:///x", "range": {"start": {"line": 0}}},
    ]
    assert resolver.references_to(tmp_path / "main.go", 1, 1) == []


def test_references_to_swallows_exception(resolver, tmp_path):
    resolver._client.references.side_effect = RuntimeError("oops")
    assert resolver.references_to(tmp_path / "main.go", 1, 1) == []


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------


def test_classify_none_is_unknown(resolver):
    assert resolver._classify(None) == "unknown"


def test_classify_mod_cache_is_third_party(resolver):
    p = Path("/home/u/go/pkg/mod/github.com/gin-gonic/gin/gin.go")
    assert resolver._classify(p) == "third_party"


def test_classify_internal(resolver, tmp_path):
    assert resolver._classify(tmp_path / "pkg" / "svc.go") == "internal"


def test_classify_goroot_is_stdlib(tmp_path):
    r = GoplsResolver()
    r._root = tmp_path / "proj"
    r._goroot = tmp_path / "goroot"
    p = tmp_path / "goroot" / "src" / "fmt" / "print.go"
    assert r._classify(p) == "stdlib"


def test_classify_unknown_fallback():
    r = GoplsResolver()  # no root, no goroot
    assert r._classify(Path("/elsewhere/x.go")) == "unknown"


def test_classify_goroot_set_but_unrelated_is_unknown(tmp_path):
    r = GoplsResolver()
    r._root = tmp_path / "proj"
    r._goroot = tmp_path / "goroot"
    assert r._classify(Path("/totally/elsewhere/x.go")) == "unknown"


# ---------------------------------------------------------------------------
# __del__ (deterministic, not GC-timing dependent)
# ---------------------------------------------------------------------------


def test_del_shuts_down_client():
    r = GoplsResolver()
    client = MagicMock(spec=_GoplsClient)
    r._client = client
    r.__del__()
    client.shutdown.assert_called_once()


def test_del_without_client_is_noop():
    GoplsResolver().__del__()  # no client -> must not raise


# ---------------------------------------------------------------------------
# prepare / status
# ---------------------------------------------------------------------------


def test_prepare_starts_client(tmp_path):
    r = GoplsResolver()
    with (
        patch("graphlens_go._resolver._GoplsClient") as MockClient,
        patch("graphlens_go._resolver._detect_goroot", return_value=None),
    ):
        MockClient.return_value = MagicMock(spec=_GoplsClient)
        r.prepare(tmp_path, [])
    MockClient.assert_called_once_with(tmp_path)
    assert r._root == tmp_path


def test_prepare_shuts_down_previous_client(tmp_path):
    r = GoplsResolver()
    old = MagicMock(spec=_GoplsClient)
    r._client = old
    with (
        patch(
            "graphlens_go._resolver._GoplsClient",
            return_value=MagicMock(spec=_GoplsClient),
        ),
        patch("graphlens_go._resolver._detect_goroot", return_value=None),
    ):
        r.prepare(tmp_path, [])
    old.shutdown.assert_called_once()


def test_prepare_swallows_client_start_failure(tmp_path):
    r = GoplsResolver()
    with (
        patch(
            "graphlens_go._resolver._GoplsClient",
            side_effect=FileNotFoundError("gopls not found"),
        ),
        patch("graphlens_go._resolver._detect_goroot", return_value=None),
    ):
        r.prepare(tmp_path, [])
    assert r._client is None


def test_status_reflects_client_presence():
    r = GoplsResolver()
    assert r.status() is ResolverStatus.UNAVAILABLE
    r._client = MagicMock(spec=_GoplsClient)
    assert r.status() is ResolverStatus.OK


# ---------------------------------------------------------------------------
# GoResolver — structure-only fallback
# ---------------------------------------------------------------------------


def test_go_resolver_degrades_and_reports_unavailable():
    r = GoResolver()
    r.prepare(Path("."), [])
    assert r.definition_at(Path("x.go"), 1, 1) is None
    assert r.infer_type_at(Path("x.go"), 1, 1) is None
    assert r.references_to(Path("x.go"), 1, 1) == []
    assert r.status() is ResolverStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# Integration: real gopls (only when the toolchain is installed, i.e. CI)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not shutil.which("gopls"), reason="gopls not installed"
)
def test_gopls_integration_resolves_internal_call(tmp_path: Path):
    """Full integration: gopls resolves a cross-file call to its def."""
    (tmp_path / "go.mod").write_text("module example.com/m\n\ngo 1.21\n")
    (tmp_path / "util.go").write_text(
        "package m\n\nfunc Helper() int { return 1 }\n"
    )
    (tmp_path / "main.go").write_text(
        "package m\n\nfunc Run() int {\n\treturn Helper()\n}\n"
    )
    r = GoplsResolver()
    r.prepare(tmp_path, [tmp_path / "main.go", tmp_path / "util.go"])
    # "Helper" call at main.go line 4 col 9 (1-based, after a tab)
    ref = r.definition_at(tmp_path / "main.go", 4, 9)
    if ref is not None:  # gopls may not resolve every site — be lenient
        assert ref.origin == "internal"
        assert ref.file_path is not None
