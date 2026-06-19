from pathlib import Path
from unittest.mock import patch

from graphlens.contracts import ResolvedRef
from graphlens_typescript._resolver import TsResolver


def test_build_request_shape():
    r = TsResolver()
    r._root = Path("/proj")
    req = r._build_request([(Path("/proj/a.ts"), 3, 5)])
    assert req == {
        "project_root": "/proj",
        "queries": [{"file": "/proj/a.ts", "line": 3, "col": 5}],
    }


def test_parse_response_maps_results():
    r = TsResolver()
    payload = {"results": [
        {"file": "/proj/b.ts", "line": 1, "col": 10,
         "name": "helper", "kind": "function", "origin": "internal"},
        None,
    ]}
    out = r._parse_response(payload)
    assert out[0] == ResolvedRef(
        full_name="helper", file_path=Path("/proj/b.ts"),
        line=1, col=10, kind="function", origin="internal")
    assert out[1] is None


def test_disabled_resolver_returns_none(tmp_path):
    r = TsResolver()
    r._disabled = True
    r._root = tmp_path
    assert r.resolve_all([(tmp_path / "a.ts", 1, 1)]) == [None]


def test_resolve_all_runs_bridge_once():
    r = TsResolver()
    r._root = Path("/proj")
    r._disabled = False
    payload = {"results": [
        {"file": "/proj/b.ts", "line": 2, "col": 3,
         "name": "f", "kind": "function", "origin": "internal"}]}
    with patch.object(r, "_run_bridge", return_value=payload) as m:
        out = r.resolve_all([(Path("/proj/a.ts"), 1, 1)])
    m.assert_called_once()
    assert out[0].full_name == "f"


def test_resolve_all_swallows_bridge_error():
    r = TsResolver()
    r._root = Path("/proj")
    r._disabled = False
    with patch.object(r, "_run_bridge", side_effect=RuntimeError("boom")):
        assert r.resolve_all([(Path("/proj/a.ts"), 1, 1)]) == [None]


def test_contract_methods_delegate():
    # cover definition_at / infer_type_at / references_to for 100%
    r = TsResolver()
    r._root = Path("/proj")
    r._disabled = False
    payload = {"results": [
        {"file": "/p/x.ts", "line": 1, "col": 1,
         "name": "f", "kind": "function", "origin": "internal"}]}
    with patch.object(r, "_run_bridge", return_value=payload):
        assert r.definition_at(Path("/proj/a.ts"), 1, 1).full_name == "f"
        assert r.infer_type_at(Path("/proj/a.ts"), 1, 1).full_name == "f"
    assert r.references_to(Path("/proj/a.ts"), 1, 1) == []


def test_run_bridge_invokes_node(tmp_path):
    # cover _run_bridge body by mocking subprocess.run (not the method)
    r = TsResolver()
    r._root = tmp_path
    r._cache_dir = tmp_path
    import json
    completed = type("C", (), {"stdout": json.dumps({"results": []}),
                               "returncode": 0})()
    with patch("graphlens_typescript._resolver.subprocess.run",
               return_value=completed) as m:
        out = r._run_bridge({"project_root": str(tmp_path), "queries": []})
    m.assert_called_once()
    assert out == {"results": []}


def test_ensure_typescript_skips_when_sentinel_present(tmp_path):
    r = TsResolver()
    sentinel = tmp_path / "node_modules" / "typescript" / "lib"
    sentinel.mkdir(parents=True)
    (sentinel / "typescript.js").write_text("")
    r._cache_dir = tmp_path
    with patch("graphlens_typescript._resolver.subprocess.run") as m:
        r.ensure_typescript()
    m.assert_not_called()  # already installed → no npm


def test_ensure_typescript_raises_when_node_missing(tmp_path):
    r = TsResolver()
    r._cache_dir = tmp_path  # sentinel absent
    with patch("graphlens_typescript._resolver.shutil.which", return_value=None):
        try:
            r.ensure_typescript()
        except RuntimeError as exc:
            assert "node/npm" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_ensure_typescript_runs_npm_install(tmp_path):
    r = TsResolver()
    r._cache_dir = tmp_path  # sentinel absent
    with patch("graphlens_typescript._resolver.shutil.which", return_value="/usr/bin/npm"), \
         patch("graphlens_typescript._resolver.subprocess.run") as m:
        r.ensure_typescript()
    m.assert_called_once()
    args = m.call_args[0][0]
    assert "npm" in args[0]
    assert "install" in args


def test_prepare_sets_root_and_calls_ensure(tmp_path):
    r = TsResolver()
    with patch.object(r, "ensure_typescript") as m:
        r.prepare(tmp_path, [])
    assert r._root == tmp_path
    m.assert_called_once()
    assert not r._disabled


def test_prepare_disables_on_ensure_failure(tmp_path):
    r = TsResolver()
    with patch.object(r, "ensure_typescript", side_effect=RuntimeError("no node")):
        r.prepare(tmp_path, [])
    assert r._disabled
