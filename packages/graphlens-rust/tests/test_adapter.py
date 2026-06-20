"""Tests for RustAdapter."""

from pathlib import Path

import pytest
from graphlens import RESOLVER_STATUS_KEY, AdapterError, NodeKind

from graphlens_rust import RustAdapter


def _kinds(graph):
    return {n.kind for n in graph.nodes.values()}


def test_meta():
    adapter = RustAdapter()
    assert adapter.language() == "rust"
    assert ".rs" in adapter.file_extensions()


def test_can_handle(tmp_path: Path):
    assert not RustAdapter().can_handle(tmp_path)
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\n')
    assert RustAdapter().can_handle(tmp_path)


def test_analyze_structural(sample_rust_project: Path):
    graph = RustAdapter().analyze(sample_rust_project)
    for expected in (
        NodeKind.PROJECT,
        NodeKind.MODULE,
        NodeKind.FILE,
        NodeKind.FUNCTION,
        NodeKind.CLASS,
        NodeKind.METHOD,
    ):
        assert expected in _kinds(graph)


def test_status_unavailable(sample_rust_project: Path):
    graph = RustAdapter().analyze(sample_rust_project)
    assert graph.metadata[RESOLVER_STATUS_KEY] == "unavailable"


def test_accepts_str_path(sample_rust_project: Path):
    graph = RustAdapter().analyze(str(sample_rust_project))
    assert len(graph.nodes) > 0


def test_strict_raises(sample_rust_project: Path):
    with pytest.raises(AdapterError, match="strict"):
        RustAdapter().analyze(sample_rust_project, strict=True)


def test_import_origins(sample_rust_project: Path):
    graph = RustAdapter().analyze(sample_rust_project)
    origins = {
        n.metadata.get("origin")
        for n in graph.nodes.values()
        if n.kind == NodeKind.EXTERNAL_SYMBOL
    }
    assert "stdlib" in origins
    assert "third_party" in origins
    assert "internal" in origins


def test_explicit_files(sample_rust_project: Path):
    files = [sample_rust_project / "src" / "lib.rs"]
    graph = RustAdapter().analyze(sample_rust_project, files=files)
    assert any(n.kind == NodeKind.FILE for n in graph.nodes.values())


def test_module_qnames(sample_rust_project: Path):
    graph = RustAdapter().analyze(sample_rust_project)
    mods = {
        n.qualified_name
        for n in graph.nodes.values()
        if n.kind == NodeKind.MODULE
    }
    assert "demo" in mods
    assert "demo::util" in mods


def test_monorepo_workspace(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["a", "b"]\n'
    )
    for name in ("a", "b"):
        crate = tmp_path / name
        (crate / "src").mkdir(parents=True)
        (crate / "Cargo.toml").write_text(f'[package]\nname="{name}"\n')
        (crate / "src" / "lib.rs").write_text("pub fn f() {}\n")
    graph = RustAdapter().analyze(tmp_path)
    projects = [
        n for n in graph.nodes.values() if n.kind == NodeKind.PROJECT
    ]
    assert len(projects) >= 2


def test_ensure_file_relative_fallback(tmp_path: Path):
    from graphlens import GraphLens

    from graphlens_rust._adapter import _ensure_file

    project_root = tmp_path / "proj"
    crate_root = tmp_path / "elsewhere"
    project_root.mkdir()
    crate_root.mkdir()
    f = crate_root / "x.rs"
    f.write_text("fn x() {}\n")
    g = GraphLens()
    file_id = _ensure_file(g, "p", project_root, crate_root, f, "mod1")
    assert g.nodes[file_id].file_path == "x.rs"


def test_module_qname_outside_crate(tmp_path: Path):
    from graphlens_rust._adapter import _module_qname

    crate = tmp_path / "crate"
    crate.mkdir()
    other = tmp_path / "other" / "f.rs"
    other.parent.mkdir()
    other.write_text("")
    assert _module_qname(other, crate, "demo") == "demo"


def test_unreadable_file_skipped(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="demo"\n')
    src = tmp_path / "src"
    src.mkdir()
    good = src / "lib.rs"
    good.write_text("pub fn a() {}\n")
    missing = src / "ghost.rs"
    graph = RustAdapter().analyze(tmp_path, files=[good, missing])
    assert any(n.name == "lib.rs" for n in graph.nodes.values())


def test_duplicate_module_deduped(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="demo"\n')
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text("pub fn a() {}\n")
    (src / "main.rs").write_text("fn main() {}\n")
    graph = RustAdapter().analyze(tmp_path)
    demo_mods = [
        n
        for n in graph.nodes.values()
        if n.kind == NodeKind.MODULE and n.qualified_name == "demo"
    ]
    assert len(demo_mods) == 1
