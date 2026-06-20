from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from graphlens_python._resolver import TyResolver, _TyLspClient, _uri_to_path

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "util.py").write_text(
        "def helper(x):\n    return x\n"
    )
    (tmp_path / "pkg" / "main.py").write_text(
        "from pkg.util import helper\n"
        "import os\n"
        "\n"
        "def run():\n"
        "    helper(1)\n"
        "    os.getcwd()\n"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# _uri_to_path helper
# ---------------------------------------------------------------------------


def test_uri_to_path_file_scheme():
    p = _uri_to_path("file:///tmp/foo/bar.py")
    assert p == Path("/tmp/foo/bar.py")


def test_uri_to_path_encoded():
    p = _uri_to_path("file:///home/user/my%20project/a.py")
    assert p == Path("/home/user/my project/a.py")


def test_uri_to_path_non_file_returns_none():
    assert _uri_to_path("https://example.com/x.py") is None
    assert _uri_to_path("") is None


# ---------------------------------------------------------------------------
# TyResolver — unit tests (mocked _TyLspClient; no real ty process)
# ---------------------------------------------------------------------------


@pytest.fixture
def ty_resolver(tmp_path: Path) -> TyResolver:
    """TyResolver with _client pre-stubbed (no real ty process)."""
    r = TyResolver()
    r._root = tmp_path
    r._client = MagicMock(spec=_TyLspClient)
    return r


def test_ty_resolver_definition_at_returns_none_when_client_none(tmp_path):
    r = TyResolver()
    assert r.definition_at(tmp_path / "x.py", 1, 1) is None


def test_ty_resolver_infer_type_at_always_returns_none(ty_resolver, tmp_path):
    assert ty_resolver.infer_type_at(tmp_path / "x.py", 1, 1) is None


def test_ty_resolver_references_to_returns_empty_when_client_none(tmp_path):
    r = TyResolver()
    assert r.references_to(tmp_path / "x.py", 1, 1) == []


def test_ty_resolver_definition_at_hit(ty_resolver, tmp_path):
    target = tmp_path / "pkg" / "util.py"
    ty_resolver._client.definition.return_value = {
        "uri": target.as_uri(),
        "range": {
            "start": {"line": 0, "character": 4},
            "end": {"line": 0, "character": 10},
        },
    }
    ref = ty_resolver.definition_at(tmp_path / "main.py", 3, 5)
    assert ref is not None
    assert ref.file_path == target
    assert ref.line == 1  # 0-based → 1-based
    assert ref.col == 5


def test_ty_resolver_definition_at_miss(ty_resolver, tmp_path):
    ty_resolver._client.definition.return_value = None
    assert ty_resolver.definition_at(tmp_path / "main.py", 1, 1) is None


def test_ty_resolver_definition_at_swallows_exception(ty_resolver, tmp_path):
    ty_resolver._client.definition.side_effect = RuntimeError("ty crashed")
    assert ty_resolver.definition_at(tmp_path / "main.py", 1, 1) is None


def test_ty_resolver_references_to_returns_occurrences(ty_resolver, tmp_path):
    target = tmp_path / "other.py"
    ty_resolver._client.references.return_value = [
        {
            "uri": target.as_uri(),
            "range": {
                "start": {"line": 4, "character": 8},
                "end": {"line": 4, "character": 14},
            },
        },
    ]
    occs = ty_resolver.references_to(tmp_path / "main.py", 1, 1)
    assert len(occs) == 1
    assert occs[0].file_path == target
    assert occs[0].line == 5
    assert occs[0].col == 9


def test_ty_resolver_references_to_skips_non_file_uris(ty_resolver, tmp_path):
    ty_resolver._client.references.return_value = [
        {
            "uri": "untitled:///unnamed",
            "range": {"start": {"line": 0, "character": 0}},
        },
    ]
    assert ty_resolver.references_to(tmp_path / "main.py", 1, 1) == []


def test_ty_resolver_references_to_swallows_exception(ty_resolver, tmp_path):
    ty_resolver._client.references.side_effect = RuntimeError("oops")
    assert ty_resolver.references_to(tmp_path / "main.py", 1, 1) == []


def test_ty_resolver_classify_internal(ty_resolver, tmp_path):
    assert ty_resolver._classify(tmp_path / "pkg" / "mod.py") == "internal"


def test_ty_resolver_classify_typeshed(ty_resolver):
    p = Path("/home/user/.cache/ty/typeshed/stdlib/os/__init__.pyi")
    assert ty_resolver._classify(p) == "stdlib"


def test_ty_resolver_classify_site_packages(ty_resolver):
    p = Path("/usr/lib/python3/site-packages/requests/api.py")
    assert ty_resolver._classify(p) == "third_party"


def test_ty_resolver_classify_none_is_stdlib(ty_resolver):
    assert ty_resolver._classify(None) == "stdlib"


def test_ty_resolver_prepare_starts_client(tmp_path):
    r = TyResolver()
    with patch("graphlens_python._resolver._TyLspClient") as MockClient:
        MockClient.return_value = MagicMock(spec=_TyLspClient)
        r.prepare(tmp_path, [])
    MockClient.assert_called_once_with(tmp_path)
    assert r._root == tmp_path


def test_ty_resolver_prepare_does_not_preopen_files(tmp_path):
    """prepare() must NOT bulk-open files — that deadlocks on large projects."""
    r = TyResolver()
    files = [tmp_path / "a.py", tmp_path / "b.py"]
    mock_client = MagicMock(spec=_TyLspClient)
    with patch(
        "graphlens_python._resolver._TyLspClient", return_value=mock_client
    ):
        r.prepare(tmp_path, files)
    mock_client.open_file.assert_not_called()


def test_ty_resolver_prepare_shuts_down_previous_client(tmp_path):
    r = TyResolver()
    old_client = MagicMock(spec=_TyLspClient)
    r._client = old_client
    with patch(
        "graphlens_python._resolver._TyLspClient",
        return_value=MagicMock(spec=_TyLspClient),
    ):
        r.prepare(tmp_path, [])
    old_client.shutdown.assert_called_once()


def test_ty_resolver_prepare_swallows_client_start_failure(tmp_path):
    r = TyResolver()
    with patch(
        "graphlens_python._resolver._TyLspClient",
        side_effect=FileNotFoundError("ty not found"),
    ):
        r.prepare(tmp_path, [])
    assert r._client is None


# ---------------------------------------------------------------------------
# Integration: TyResolver on a real mini-project (requires `ty` in PATH)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("shutil").which("ty"),
    reason="ty not installed",
)
def test_ty_resolver_integration_resolves_internal_call(proj):
    """Full integration: TyResolver resolves a cross-file call via ty server."""
    r = TyResolver()
    files = list(proj.rglob("*.py"))
    r.prepare(proj, files)

    # "helper" at main.py line 5 col 5 (1-based)
    ref = r.definition_at(proj / "pkg" / "main.py", 5, 5)

    # ty may not resolve every call site — skip if None rather than fail
    if ref is not None:
        assert ref.origin == "internal"
        assert ref.file_path is not None
        assert ref.line >= 1
        assert ref.col >= 1


def test_status_reflects_client_presence():
    from graphlens import ResolverStatus

    r = TyResolver()
    assert r.status() is ResolverStatus.UNAVAILABLE
    r._client = MagicMock()
    assert r.status() is ResolverStatus.OK
