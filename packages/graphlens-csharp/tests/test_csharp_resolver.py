from pathlib import Path
from unittest.mock import MagicMock, patch

from graphlens import ResolverStatus

from graphlens_csharp._resolver import (
    CsharpLspResolver,
    _CsharpLspClient,
    _uri_to_path,
)

# ---------------------------------------------------------------------------
# _uri_to_path
# ---------------------------------------------------------------------------


def test_uri_to_path_file_scheme():
    assert _uri_to_path("file:///tmp/foo/Bar.cs") == Path("/tmp/foo/Bar.cs")


def test_uri_to_path_encoded():
    assert _uri_to_path("file:///home/u/my%20app/A.cs") == Path(
        "/home/u/my app/A.cs"
    )


def test_uri_to_path_non_file():
    assert _uri_to_path("csharp:/metadata/System.String") is None
    assert _uri_to_path("") is None


# ---------------------------------------------------------------------------
# Spawn command
# ---------------------------------------------------------------------------


def test_spawn_argv_env_override(monkeypatch):
    monkeypatch.setenv("GRAPHLENS_CSHARP_LS", "/opt/csharp-ls")
    assert CsharpLspResolver()._spawn_argv() == ["/opt/csharp-ls"]


def test_spawn_argv_default(monkeypatch):
    monkeypatch.delenv("GRAPHLENS_CSHARP_LS", raising=False)
    monkeypatch.setattr(
        "graphlens_csharp._resolver.shutil.which", lambda _name: None
    )
    assert CsharpLspResolver()._spawn_argv() == ["csharp-ls"]


# ---------------------------------------------------------------------------
# CsharpLspResolver (mocked client)
# ---------------------------------------------------------------------------


def _resolver(tmp_path: Path):
    r = CsharpLspResolver()
    r._root = tmp_path
    r._client = MagicMock(spec=_CsharpLspClient)
    return r


def test_definition_at_none_when_no_client(tmp_path):
    assert CsharpLspResolver().definition_at(tmp_path / "A.cs", 1, 1) is None


def test_infer_type_at_always_none(tmp_path):
    assert _resolver(tmp_path).infer_type_at(tmp_path / "A.cs", 1, 1) is None


def test_references_to_empty_when_no_client(tmp_path):
    assert CsharpLspResolver().references_to(tmp_path / "A.cs", 1, 1) == []


def test_definition_at_hit(tmp_path):
    r = _resolver(tmp_path)
    target = tmp_path / "src" / "User.cs"
    r._client.definition.return_value = {
        "uri": target.as_uri(),
        "range": {"start": {"line": 4, "character": 6}},
    }
    ref = r.definition_at(tmp_path / "Main.cs", 2, 3)
    assert ref is not None
    assert ref.file_path == target
    assert ref.line == 5
    assert ref.col == 7
    assert ref.origin == "internal"


def test_definition_at_miss(tmp_path):
    r = _resolver(tmp_path)
    r._client.definition.return_value = None
    assert r.definition_at(tmp_path / "Main.cs", 1, 1) is None


def test_definition_at_swallows_exception(tmp_path):
    r = _resolver(tmp_path)
    r._client.definition.side_effect = RuntimeError("boom")
    assert r.definition_at(tmp_path / "Main.cs", 1, 1) is None


def test_resolve_all_none_when_no_client(tmp_path):
    out = CsharpLspResolver().resolve_all(
        [(tmp_path / "A.cs", 1, 1), (tmp_path / "B.cs", 2, 2)]
    )
    assert out == [None, None]


def test_resolve_all_batches_and_maps(tmp_path):
    r = _resolver(tmp_path)
    target = tmp_path / "src" / "User.cs"
    loc = {
        "uri": target.as_uri(),
        "range": {"start": {"line": 4, "character": 6}},
    }
    r._client.definition_batch.return_value = [loc, None]
    queries = [(tmp_path / "Main.cs", 2, 3), (tmp_path / "Main.cs", 9, 1)]
    out = r.resolve_all(queries)
    r._client.definition_batch.assert_called_once_with(queries)
    assert out[0] is not None
    assert out[0].file_path == target
    assert out[0].origin == "internal"
    assert out[1] is None


def test_resolve_all_swallows_exception(tmp_path):
    r = _resolver(tmp_path)
    r._client.definition_batch.side_effect = RuntimeError("boom")
    assert r.resolve_all([(tmp_path / "A.cs", 1, 1)]) == [None]


def test_references_to_occurrences(tmp_path):
    r = _resolver(tmp_path)
    target = tmp_path / "Other.cs"
    r._client.references.return_value = [
        {
            "uri": target.as_uri(),
            "range": {"start": {"line": 3, "character": 2}},
        },
    ]
    occs = r.references_to(tmp_path / "Main.cs", 1, 1)
    assert len(occs) == 1
    assert occs[0].file_path == target
    assert occs[0].line == 4
    assert occs[0].col == 3


def test_references_to_skips_non_file(tmp_path):
    r = _resolver(tmp_path)
    r._client.references.return_value = [
        {"uri": "csharp:/metadata/X", "range": {"start": {}}},
    ]
    assert r.references_to(tmp_path / "Main.cs", 1, 1) == []


def test_references_to_swallows_exception(tmp_path):
    r = _resolver(tmp_path)
    r._client.references.side_effect = RuntimeError("oops")
    assert r.references_to(tmp_path / "Main.cs", 1, 1) == []


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------


def test_classify_none_is_unknown(tmp_path):
    assert _resolver(tmp_path)._classify(None) == "unknown"


def test_classify_nuget_third_party(tmp_path):
    r = _resolver(tmp_path)
    p = Path.home() / ".nuget" / "packages" / "serilog" / "Log.cs"
    assert r._classify(p) == "third_party"


def test_classify_packages_third_party(tmp_path):
    r = _resolver(tmp_path)
    p = tmp_path.parent / "packages" / "NLog" / "Logger.cs"
    assert r._classify(p) == "third_party"


def test_classify_internal(tmp_path):
    r = _resolver(tmp_path)
    assert r._classify(tmp_path / "src" / "User.cs") == "internal"


def test_classify_unknown_when_outside_root():
    r = CsharpLspResolver()
    r._client = MagicMock(spec=_CsharpLspClient)
    r._root = None
    assert r._classify(Path("/elsewhere/X.cs")) == "unknown"


# ---------------------------------------------------------------------------
# status / prepare / lifecycle
# ---------------------------------------------------------------------------


def test_status_reflects_client_presence():
    r = CsharpLspResolver()
    assert r.status() is ResolverStatus.UNAVAILABLE
    r._client = MagicMock(spec=_CsharpLspClient)
    assert r.status() is ResolverStatus.OK


def test_prepare_starts_client(tmp_path):
    r = CsharpLspResolver()
    with patch("graphlens_csharp._resolver._CsharpLspClient") as Mock:
        Mock.return_value = MagicMock(spec=_CsharpLspClient)
        r.prepare(tmp_path, [])
    Mock.assert_called_once_with(tmp_path, r._spawn_argv(), name=r._engine)
    assert r._root == tmp_path


def test_prepare_shuts_down_previous_client(tmp_path):
    r = CsharpLspResolver()
    old = MagicMock(spec=_CsharpLspClient)
    r._client = old
    with patch(
        "graphlens_csharp._resolver._CsharpLspClient",
        return_value=MagicMock(spec=_CsharpLspClient),
    ):
        r.prepare(tmp_path, [])
    old.shutdown.assert_called_once()


def test_prepare_swallows_start_failure(tmp_path):
    r = CsharpLspResolver()
    with patch(
        "graphlens_csharp._resolver._CsharpLspClient",
        side_effect=FileNotFoundError("server missing"),
    ):
        r.prepare(tmp_path, [])
    assert r._client is None
    assert r.status() is ResolverStatus.UNAVAILABLE


def test_del_with_client_shuts_down(tmp_path):
    r = _resolver(tmp_path)
    client = r._client
    r.__del__()
    client.shutdown.assert_called_once()


def test_del_with_no_client_is_a_noop():
    CsharpLspResolver().__del__()
