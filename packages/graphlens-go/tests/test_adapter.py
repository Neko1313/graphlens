"""Tests for GoAdapter."""

from pathlib import Path

import pytest
from graphlens import RESOLVER_STATUS_KEY, AdapterError, NodeKind

from graphlens_go import GoAdapter


def _kinds(graph):
    return {n.kind for n in graph.nodes.values()}


def test_meta():
    adapter = GoAdapter()
    assert adapter.language() == "go"
    assert ".go" in adapter.file_extensions()


def test_can_handle(tmp_path: Path):
    assert not GoAdapter().can_handle(tmp_path)
    (tmp_path / "go.mod").write_text("module x\n")
    assert GoAdapter().can_handle(tmp_path)


def test_analyze_structural(sample_go_project: Path):
    graph = GoAdapter().analyze(sample_go_project)
    kinds = _kinds(graph)
    for expected in (
        NodeKind.PROJECT,
        NodeKind.MODULE,
        NodeKind.FILE,
        NodeKind.FUNCTION,
        NodeKind.METHOD,
        NodeKind.CLASS,
    ):
        assert expected in kinds


def test_analyze_status_unavailable(sample_go_project: Path):
    graph = GoAdapter().analyze(sample_go_project)
    assert graph.metadata[RESOLVER_STATUS_KEY] == "unavailable"


def test_analyze_accepts_str_path(sample_go_project: Path):
    graph = GoAdapter().analyze(str(sample_go_project))
    assert len(graph.nodes) > 0


def test_strict_raises_when_unavailable(sample_go_project: Path):
    with pytest.raises(AdapterError, match="strict"):
        GoAdapter().analyze(sample_go_project, strict=True)


def test_import_origins(sample_go_project: Path):
    graph = GoAdapter().analyze(sample_go_project)
    origins = {
        n.metadata.get("origin")
        for n in graph.nodes.values()
        if n.kind == NodeKind.EXTERNAL_SYMBOL
    }
    assert "stdlib" in origins
    assert "third_party" in origins
    assert "internal" in origins


def test_explicit_files(sample_go_project: Path):
    files = [sample_go_project / "main.go"]
    graph = GoAdapter().analyze(sample_go_project, files=files)
    assert any(n.kind == NodeKind.FILE for n in graph.nodes.values())


def test_monorepo_multiple_projects(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module root\n")
    (tmp_path / "a.go").write_text("package main\nfunc A() {}\n")
    sub = tmp_path / "svc"
    sub.mkdir()
    (sub / "go.mod").write_text("module root/svc\n")
    (sub / "b.go").write_text("package svc\nfunc B() {}\n")

    graph = GoAdapter().analyze(tmp_path)
    projects = [
        n for n in graph.nodes.values() if n.kind == NodeKind.PROJECT
    ]
    assert len(projects) >= 2


def test_unreadable_file_skipped(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module root\n")
    good = tmp_path / "a.go"
    good.write_text("package main\nfunc A() {}\n")
    missing = tmp_path / "ghost.go"
    graph = GoAdapter().analyze(tmp_path, files=[good, missing])
    assert any(n.name == "a.go" for n in graph.nodes.values())


def test_ensure_file_relative_fallback(tmp_path):
    from graphlens import GraphLens

    from graphlens_go._adapter import _ensure_file

    project_root = tmp_path / "proj"
    go_root = tmp_path / "elsewhere"
    project_root.mkdir()
    go_root.mkdir()
    f = go_root / "x.go"
    f.write_text("package x\n")
    g = GraphLens()
    file_id = _ensure_file(g, "p", project_root, go_root, f, "mod1")
    assert file_id in g.nodes
    assert g.nodes[file_id].file_path == "x.go"
