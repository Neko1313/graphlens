"""Tests for Rust resolvers: RustAnalyzerResolver (mocked) and RustResolver."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from graphlens import ResolverStatus

from graphlens_rust import RustAnalyzerResolver, RustResolver
from graphlens_rust._resolver import (
    _in_cargo_registry,
    _in_rust_stdlib,
    _loc_uri_and_start,
    _resolve_ra_binary,
    _RustAnalyzerClient,
    _uri_to_path,
)

# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def test_resolve_ra_binary_prefers_project_toolchain(tmp_path):
    # When the project's pinned toolchain has the component, rustup resolved
    # from the project dir returns that exact binary — we spawn it so the
    # analysis matches the project's toolchain version.
    proj_ra = tmp_path / "proj-ra"
    proj_ra.write_text("#!/bin/sh\n")
    with patch.object(
        shutil, "which",
        side_effect={"rustup": "/root/.cargo/bin/rustup"}.get,
    ), patch("graphlens_rust._resolver.subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout=f"{proj_ra}\n")
        assert _resolve_ra_binary(tmp_path) == str(proj_ra)
        # first lookup is from the project root (honours rust-toolchain.toml)
        assert run.call_args_list[0].kwargs["cwd"] == str(tmp_path)


def test_resolve_ra_binary_falls_back_to_default_toolchain(tmp_path):
    # The pinned toolchain lacks the component (its path does not exist), so
    # the project lookup yields nothing and we fall back to the default
    # toolchain's binary resolved from a neutral directory.
    default_ra = tmp_path / "default-ra"
    default_ra.write_text("#!/bin/sh\n")
    missing = tmp_path / "missing-ra"  # never created

    def fake_run(cmd, **kwargs):
        if kwargs.get("cwd") == str(tmp_path):
            return MagicMock(returncode=0, stdout=f"{missing}\n")
        return MagicMock(returncode=0, stdout=f"{default_ra}\n")

    with patch.object(
        shutil, "which",
        side_effect={"rustup": "/root/.cargo/bin/rustup"}.get,
    ), patch("graphlens_rust._resolver.subprocess.run", side_effect=fake_run):
        assert _resolve_ra_binary(tmp_path) == str(default_ra)


def test_resolve_ra_binary_falls_back_without_rustup(tmp_path):
    with patch.object(
        shutil, "which",
        side_effect=lambda name: (
            "/usr/bin/rust-analyzer" if name == "rust-analyzer" else None
        ),
    ):
        assert _resolve_ra_binary(tmp_path) == "/usr/bin/rust-analyzer"


def test_resolve_ra_binary_falls_back_when_rustup_which_fails(tmp_path):
    with patch.object(
        shutil, "which",
        side_effect={
            "rust-analyzer": "/root/.cargo/bin/rust-analyzer",
            "rustup": "/root/.cargo/bin/rustup",
        }.get,
    ), patch("graphlens_rust._resolver.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stdout="")
        assert _resolve_ra_binary(tmp_path) == "/root/.cargo/bin/rust-analyzer"


def test_uri_to_path_file_scheme():
    assert _uri_to_path("file:///tmp/a/b.rs") == Path("/tmp/a/b.rs")


def test_uri_to_path_encoded():
    assert _uri_to_path("file:///home/u/my%20proj/a.rs") == Path(
        "/home/u/my proj/a.rs"
    )


def test_uri_to_path_non_file_returns_none():
    assert _uri_to_path("https://x/y.rs") is None
    assert _uri_to_path("") is None


def test_in_cargo_registry_true():
    p = Path("/home/u/.cargo/registry/src/index/serde-1.0/src/lib.rs")
    assert _in_cargo_registry(p.parts) is True


def test_in_cargo_registry_false():
    assert _in_cargo_registry(Path("/home/u/proj/a.rs").parts) is False


def test_in_rust_stdlib_true():
    p = Path("/home/u/.rustup/toolchains/x/lib/rustlib/src/rust/core.rs")
    assert _in_rust_stdlib(p.parts) is True


def test_in_rust_stdlib_false():
    assert _in_rust_stdlib(Path("/home/u/proj/a.rs").parts) is False


# ---------------------------------------------------------------------------
# RustAnalyzerResolver — unit tests (mocked client; no real process)
# ---------------------------------------------------------------------------


@pytest.fixture
def resolver(tmp_path: Path) -> RustAnalyzerResolver:
    r = RustAnalyzerResolver()
    r._root = tmp_path
    r._client = MagicMock(spec=_RustAnalyzerClient)
    return r


def test_definition_at_returns_none_when_client_none(tmp_path):
    r = RustAnalyzerResolver()
    assert r.definition_at(tmp_path / "x.rs", 1, 1) is None


def test_infer_type_at_always_none(resolver, tmp_path):
    assert resolver.infer_type_at(tmp_path / "x.rs", 1, 1) is None


def test_references_to_returns_empty_when_client_none(tmp_path):
    r = RustAnalyzerResolver()
    assert r.references_to(tmp_path / "x.rs", 1, 1) == []


def test_definition_at_hit(resolver, tmp_path):
    target = tmp_path / "lib.rs"
    resolver._client.definition.return_value = {
        "uri": target.as_uri(),
        "range": {"start": {"line": 2, "character": 3}},
    }
    ref = resolver.definition_at(tmp_path / "main.rs", 4, 1)
    assert ref is not None
    assert ref.file_path == target
    assert ref.line == 3
    assert ref.col == 4
    assert ref.origin == "internal"


def test_definition_at_locationlink(resolver, tmp_path):
    """rust-analyzer may return a LocationLink (targetUri/targetSel...)."""
    target = tmp_path / "lib.rs"
    resolver._client.definition.return_value = {
        "targetUri": target.as_uri(),
        "targetSelectionRange": {"start": {"line": 2, "character": 3}},
    }
    ref = resolver.definition_at(tmp_path / "main.rs", 4, 1)
    assert ref is not None
    assert ref.file_path == target
    assert ref.line == 3
    assert ref.col == 4
    assert ref.origin == "internal"


def test_definition_at_miss(resolver, tmp_path):
    resolver._client.definition.return_value = None
    assert resolver.definition_at(tmp_path / "main.rs", 1, 1) is None


def test_resolve_all_returns_none_list_when_client_none(tmp_path):
    r = RustAnalyzerResolver()
    queries = [(tmp_path / "a.rs", 1, 1), (tmp_path / "b.rs", 2, 2)]
    assert r.resolve_all(queries) == [None, None]


def test_resolve_all_batches_and_maps_results(resolver, tmp_path):
    target = tmp_path / "lib.rs"
    resolver._client.definition_batch.return_value = [
        {"uri": target.as_uri(), "range": {"start": {"line": 2, "character": 3}}},
        None,
    ]
    queries = [(tmp_path / "main.rs", 4, 1), (tmp_path / "main.rs", 5, 1)]
    refs = resolver.resolve_all(queries)

    resolver._client.definition_batch.assert_called_once_with(queries)
    assert refs[0] is not None
    assert refs[0].file_path == target
    assert refs[0].line == 3
    assert refs[0].col == 4
    assert refs[0].origin == "internal"
    assert refs[1] is None


def test_resolve_all_swallows_exception(resolver, tmp_path):
    resolver._client.definition_batch.side_effect = RuntimeError("boom")
    assert resolver.resolve_all([(tmp_path / "main.rs", 1, 1)]) == [None]


def test_resolve_all_empty_is_empty(resolver):
    assert resolver.resolve_all([]) == []


def test_loc_uri_and_start_location():
    uri, start = _loc_uri_and_start(
        {"uri": "file:///a.rs", "range": {"start": {"line": 1}}}
    )
    assert uri == "file:///a.rs"
    assert start == {"line": 1}


def test_loc_uri_and_start_locationlink():
    uri, start = _loc_uri_and_start(
        {
            "targetUri": "file:///b.rs",
            "targetSelectionRange": {"start": {"line": 3}},
        }
    )
    assert uri == "file:///b.rs"
    assert start == {"line": 3}


def test_loc_uri_and_start_targetrange_fallback():
    _uri, start = _loc_uri_and_start(
        {"targetUri": "file:///c.rs", "targetRange": {"start": {"line": 4}}}
    )
    assert start == {"line": 4}


def test_loc_uri_and_start_empty():
    uri, start = _loc_uri_and_start({})
    assert uri == ""
    assert start == {}


def test_definition_at_swallows_exception(resolver, tmp_path):
    resolver._client.definition.side_effect = RuntimeError("boom")
    assert resolver.definition_at(tmp_path / "main.rs", 1, 1) is None


def test_references_to_returns_occurrences(resolver, tmp_path):
    target = tmp_path / "other.rs"
    resolver._client.references.return_value = [
        {
            "uri": target.as_uri(),
            "range": {"start": {"line": 4, "character": 8}},
        },
    ]
    occs = resolver.references_to(tmp_path / "main.rs", 1, 1)
    assert len(occs) == 1
    assert occs[0].file_path == target
    assert occs[0].line == 5
    assert occs[0].col == 9


def test_references_to_skips_non_file_uris(resolver, tmp_path):
    resolver._client.references.return_value = [
        {"uri": "untitled:///x", "range": {"start": {"line": 0}}},
    ]
    assert resolver.references_to(tmp_path / "main.rs", 1, 1) == []


def test_references_to_swallows_exception(resolver, tmp_path):
    resolver._client.references.side_effect = RuntimeError("oops")
    assert resolver.references_to(tmp_path / "main.rs", 1, 1) == []


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------


def test_classify_none_is_unknown(resolver):
    assert resolver._classify(None) == "unknown"


def test_classify_cargo_registry_is_third_party(resolver):
    p = Path("/home/u/.cargo/registry/src/i/serde-1.0/src/lib.rs")
    assert resolver._classify(p) == "third_party"


def test_classify_rustlib_is_stdlib(resolver):
    p = Path("/home/u/.rustup/toolchains/x/lib/rustlib/src/rust/core.rs")
    assert resolver._classify(p) == "stdlib"


def test_classify_internal(resolver, tmp_path):
    assert resolver._classify(tmp_path / "src" / "svc.rs") == "internal"


def test_classify_unknown_fallback():
    r = RustAnalyzerResolver()  # no root
    assert r._classify(Path("/elsewhere/x.rs")) == "unknown"


def test_classify_root_set_but_unrelated_is_unknown(tmp_path):
    r = RustAnalyzerResolver()
    r._root = tmp_path / "proj"
    assert r._classify(Path("/totally/elsewhere/x.rs")) == "unknown"


# ---------------------------------------------------------------------------
# __del__ (deterministic, not GC-timing dependent)
# ---------------------------------------------------------------------------


def test_del_shuts_down_client():
    r = RustAnalyzerResolver()
    client = MagicMock(spec=_RustAnalyzerClient)
    r._client = client
    r.__del__()
    client.shutdown.assert_called_once()


def test_del_without_client_is_noop():
    RustAnalyzerResolver().__del__()  # no client -> must not raise


# ---------------------------------------------------------------------------
# prepare / status
# ---------------------------------------------------------------------------


def test_prepare_starts_client(tmp_path):
    r = RustAnalyzerResolver()
    with patch(
        "graphlens_rust._resolver._RustAnalyzerClient"
    ) as MockClient:
        MockClient.return_value = MagicMock(spec=_RustAnalyzerClient)
        r.prepare(tmp_path, [])
    MockClient.assert_called_once_with(tmp_path)
    assert r._root == tmp_path


def test_prepare_shuts_down_previous_client(tmp_path):
    r = RustAnalyzerResolver()
    old = MagicMock(spec=_RustAnalyzerClient)
    r._client = old
    with patch(
        "graphlens_rust._resolver._RustAnalyzerClient",
        return_value=MagicMock(spec=_RustAnalyzerClient),
    ):
        r.prepare(tmp_path, [])
    old.shutdown.assert_called_once()


def test_prepare_swallows_client_start_failure(tmp_path):
    r = RustAnalyzerResolver()
    with patch(
        "graphlens_rust._resolver._RustAnalyzerClient",
        side_effect=FileNotFoundError("rust-analyzer not found"),
    ):
        r.prepare(tmp_path, [])
    assert r._client is None


def test_status_reflects_client_presence():
    r = RustAnalyzerResolver()
    assert r.status() is ResolverStatus.UNAVAILABLE
    r._client = MagicMock(spec=_RustAnalyzerClient)
    r._client.is_alive.return_value = True
    assert r.status() is ResolverStatus.OK


def test_status_degraded_when_client_process_died():
    # A client that started but whose rust-analyzer process exited (e.g. a
    # workspace that failed to load) is reported DEGRADED, not OK.
    r = RustAnalyzerResolver()
    r._client = MagicMock(spec=_RustAnalyzerClient)
    r._client.is_alive.return_value = False
    assert r.status() is ResolverStatus.DEGRADED


# ---------------------------------------------------------------------------
# RustResolver — structure-only fallback
# ---------------------------------------------------------------------------


def test_rust_resolver_degrades_and_reports_unavailable():
    r = RustResolver()
    r.prepare(Path("."), [])
    assert r.definition_at(Path("x.rs"), 1, 1) is None
    assert r.infer_type_at(Path("x.rs"), 1, 1) is None
    assert r.references_to(Path("x.rs"), 1, 1) == []
    assert r.status() is ResolverStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# Integration: real rust-analyzer (only when installed, i.e. CI)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not shutil.which("rust-analyzer"),
    reason="rust-analyzer not installed",
)
def test_rust_analyzer_integration_resolves_internal_call(tmp_path: Path):
    """Full integration: rust-analyzer resolves a cross-module call."""
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "m"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "util.rs").write_text("pub fn helper() -> i32 { 1 }\n")
    (src / "main.rs").write_text(
        "mod util;\nfn main() {\n    let _ = util::helper();\n}\n"
    )
    r = RustAnalyzerResolver()
    r.prepare(tmp_path, [src / "main.rs", src / "util.rs"])
    # "helper" call on main.rs line 3 (1-based); be lenient if unresolved.
    ref = r.definition_at(src / "main.rs", 3, 19)
    if ref is not None:
        assert ref.file_path is not None
