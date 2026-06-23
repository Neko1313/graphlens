from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from graphlens import ResolverStatus

from graphlens_php._resolver import (
    PhpactorResolver,
    PhpantomResolver,
    PhpResolver,
    _LspResolver,
    _PhpLspClient,
    _uri_to_path,
)

# ---------------------------------------------------------------------------
# _uri_to_path
# ---------------------------------------------------------------------------


def test_uri_to_path_file_scheme():
    assert _uri_to_path("file:///tmp/foo/Bar.php") == Path("/tmp/foo/Bar.php")


def test_uri_to_path_encoded():
    assert _uri_to_path("file:///home/u/my%20app/A.php") == Path(
        "/home/u/my app/A.php"
    )


def test_uri_to_path_non_file():
    assert _uri_to_path("https://x/y.php") is None
    assert _uri_to_path("") is None


# ---------------------------------------------------------------------------
# Spawn commands — engine selection
# ---------------------------------------------------------------------------


def test_phpantom_spawn_argv_env_override(monkeypatch):
    monkeypatch.setenv("GRAPHLENS_PHPANTOM", "/opt/phpantom_lsp")
    assert PhpantomResolver()._spawn_argv() == ["/opt/phpantom_lsp", "--stdio"]


def test_phpantom_spawn_argv_default(monkeypatch):
    monkeypatch.delenv("GRAPHLENS_PHPANTOM", raising=False)
    monkeypatch.setattr(
        "graphlens_php._resolver.shutil.which", lambda _name: None
    )
    assert PhpantomResolver()._spawn_argv() == ["phpantom_lsp", "--stdio"]


def test_phpactor_spawn_argv_env_override(monkeypatch):
    monkeypatch.setenv("GRAPHLENS_PHPACTOR", "/opt/phpactor")
    assert PhpactorResolver()._spawn_argv() == [
        "/opt/phpactor",
        "language-server",
        "--no-ansi",
    ]


def test_base_spawn_argv_not_implemented():
    with pytest.raises(NotImplementedError):
        _LspResolver()._spawn_argv()


# ---------------------------------------------------------------------------
# LSP resolvers (mocked client) — exercised through both engines
# ---------------------------------------------------------------------------

RESOLVERS = [PhpantomResolver, PhpactorResolver]


def _resolver(cls, tmp_path: Path):
    r = cls()
    r._root = tmp_path
    r._client = MagicMock(spec=_PhpLspClient)
    return r


@pytest.mark.parametrize("cls", RESOLVERS)
def test_definition_at_none_when_no_client(cls, tmp_path: Path):
    assert cls().definition_at(tmp_path / "A.php", 1, 1) is None


@pytest.mark.parametrize("cls", RESOLVERS)
def test_infer_type_at_always_none(cls, tmp_path: Path):
    assert (
        _resolver(cls, tmp_path).infer_type_at(tmp_path / "A.php", 1, 1)
        is None
    )


@pytest.mark.parametrize("cls", RESOLVERS)
def test_references_to_empty_when_no_client(cls, tmp_path: Path):
    assert cls().references_to(tmp_path / "A.php", 1, 1) == []


@pytest.mark.parametrize("cls", RESOLVERS)
def test_definition_at_hit(cls, tmp_path: Path):
    r = _resolver(cls, tmp_path)
    target = tmp_path / "src" / "User.php"
    r._client.definition.return_value = {
        "uri": target.as_uri(),
        "range": {
            "start": {"line": 4, "character": 6},
            "end": {"line": 4, "character": 10},
        },
    }
    ref = r.definition_at(tmp_path / "Main.php", 2, 3)
    assert ref is not None
    assert ref.file_path == target
    assert ref.line == 5
    assert ref.col == 7
    assert ref.origin == "internal"


@pytest.mark.parametrize("cls", RESOLVERS)
def test_definition_at_miss(cls, tmp_path: Path):
    r = _resolver(cls, tmp_path)
    r._client.definition.return_value = None
    assert r.definition_at(tmp_path / "Main.php", 1, 1) is None


@pytest.mark.parametrize("cls", RESOLVERS)
def test_definition_at_swallows_exception(cls, tmp_path: Path):
    r = _resolver(cls, tmp_path)
    r._client.definition.side_effect = RuntimeError("boom")
    assert r.definition_at(tmp_path / "Main.php", 1, 1) is None


@pytest.mark.parametrize("cls", RESOLVERS)
def test_resolve_all_none_when_no_client(cls, tmp_path: Path):
    out = cls().resolve_all(
        [(tmp_path / "A.php", 1, 1), (tmp_path / "B.php", 2, 2)]
    )
    assert out == [None, None]


@pytest.mark.parametrize("cls", RESOLVERS)
def test_resolve_all_batches_and_maps(cls, tmp_path: Path):
    r = _resolver(cls, tmp_path)
    target = tmp_path / "src" / "User.php"
    loc = {
        "uri": target.as_uri(),
        "range": {
            "start": {"line": 4, "character": 6},
            "end": {"line": 4, "character": 10},
        },
    }
    # One hit, one miss — order preserved, miss stays None.
    r._client.definition_batch.return_value = [loc, None]
    queries = [(tmp_path / "Main.php", 2, 3), (tmp_path / "Main.php", 9, 1)]
    out = r.resolve_all(queries)
    r._client.definition_batch.assert_called_once_with(queries)
    assert out[0] is not None
    assert out[0].file_path == target
    assert out[0].line == 5
    assert out[0].col == 7
    assert out[0].origin == "internal"
    assert out[1] is None


@pytest.mark.parametrize("cls", RESOLVERS)
def test_resolve_all_swallows_exception(cls, tmp_path: Path):
    r = _resolver(cls, tmp_path)
    r._client.definition_batch.side_effect = RuntimeError("boom")
    assert r.resolve_all([(tmp_path / "A.php", 1, 1)]) == [None]


@pytest.mark.parametrize("cls", RESOLVERS)
def test_references_to_occurrences(cls, tmp_path: Path):
    r = _resolver(cls, tmp_path)
    target = tmp_path / "Other.php"
    r._client.references.return_value = [
        {
            "uri": target.as_uri(),
            "range": {"start": {"line": 3, "character": 2}},
        },
    ]
    occs = r.references_to(tmp_path / "Main.php", 1, 1)
    assert len(occs) == 1
    assert occs[0].file_path == target
    assert occs[0].line == 4
    assert occs[0].col == 3


@pytest.mark.parametrize("cls", RESOLVERS)
def test_references_to_skips_non_file(cls, tmp_path: Path):
    r = _resolver(cls, tmp_path)
    r._client.references.return_value = [
        {"uri": "untitled:///x", "range": {"start": {}}},
    ]
    assert r.references_to(tmp_path / "Main.php", 1, 1) == []


@pytest.mark.parametrize("cls", RESOLVERS)
def test_references_to_swallows_exception(cls, tmp_path: Path):
    r = _resolver(cls, tmp_path)
    r._client.references.side_effect = RuntimeError("oops")
    assert r.references_to(tmp_path / "Main.php", 1, 1) == []


@pytest.mark.parametrize("cls", RESOLVERS)
def test_classify_third_party(cls, tmp_path: Path):
    r = _resolver(cls, tmp_path)
    p = tmp_path / "vendor" / "monolog" / "src" / "Logger.php"
    assert r._classify(p) == "third_party"


@pytest.mark.parametrize("cls", RESOLVERS)
def test_classify_internal(cls, tmp_path: Path):
    r = _resolver(cls, tmp_path)
    assert r._classify(tmp_path / "src" / "User.php") == "internal"


@pytest.mark.parametrize("cls", RESOLVERS)
def test_classify_none_is_stdlib(cls, tmp_path: Path):
    assert _resolver(cls, tmp_path)._classify(None) == "stdlib"


@pytest.mark.parametrize("cls", RESOLVERS)
def test_classify_unknown_when_outside_root(cls, tmp_path: Path):
    r = cls()
    r._client = MagicMock(spec=_PhpLspClient)
    r._root = None
    assert r._classify(Path("/elsewhere/X.php")) == "unknown"


@pytest.mark.parametrize("cls", RESOLVERS)
def test_prepare_starts_client(cls, tmp_path: Path):
    r = cls()
    with patch("graphlens_php._resolver._PhpLspClient") as Mock:
        Mock.return_value = MagicMock(spec=_PhpLspClient)
        r.prepare(tmp_path, [])
    Mock.assert_called_once_with(tmp_path, r._spawn_argv(), name=r._engine)
    assert r._root == tmp_path


@pytest.mark.parametrize("cls", RESOLVERS)
def test_prepare_shuts_down_previous_client(cls, tmp_path: Path):
    r = cls()
    old = MagicMock(spec=_PhpLspClient)
    r._client = old
    with patch(
        "graphlens_php._resolver._PhpLspClient",
        return_value=MagicMock(spec=_PhpLspClient),
    ):
        r.prepare(tmp_path, [])
    old.shutdown.assert_called_once()


@pytest.mark.parametrize("cls", RESOLVERS)
def test_prepare_swallows_start_failure(cls, tmp_path: Path):
    r = cls()
    with patch(
        "graphlens_php._resolver._PhpLspClient",
        side_effect=FileNotFoundError("server missing"),
    ):
        r.prepare(tmp_path, [])
    assert r._client is None


@pytest.mark.parametrize("cls", RESOLVERS)
def test_status_reflects_client_presence(cls):
    r = cls()
    assert r.status() is ResolverStatus.UNAVAILABLE
    r._client = MagicMock(spec=_PhpLspClient)
    assert r.status() is ResolverStatus.OK


@pytest.mark.parametrize("cls", RESOLVERS)
def test_del_with_client_shuts_down(cls, tmp_path: Path):
    r = _resolver(cls, tmp_path)
    client = r._client
    r.__del__()
    client.shutdown.assert_called_once()


# ---------------------------------------------------------------------------
# PhpResolver (structure-only fallback)
# ---------------------------------------------------------------------------


def test_php_resolver_is_unavailable(tmp_path: Path):
    r = PhpResolver()
    r.prepare(tmp_path, [])
    assert r.status() is ResolverStatus.UNAVAILABLE
    assert r.definition_at(tmp_path / "A.php", 1, 1) is None
    assert r.infer_type_at(tmp_path / "A.php", 1, 1) is None
    assert r.references_to(tmp_path / "A.php", 1, 1) == []
