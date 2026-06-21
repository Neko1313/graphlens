"""Tests for Go project detection."""

from graphlens_go._project_detector import (
    detect_project_name,
    find_go_roots,
    is_go_project,
)


def test_is_go_project(tmp_path):
    assert not is_go_project(tmp_path)
    (tmp_path / "go.mod").write_text("module x\n")
    assert is_go_project(tmp_path)


def test_find_go_roots_monorepo_excludes_vendor(tmp_path):
    (tmp_path / "go.mod").write_text("module root\n")
    sub = tmp_path / "svc"
    sub.mkdir()
    (sub / "go.mod").write_text("module root/svc\n")
    vendored = tmp_path / "vendor" / "dep"
    vendored.mkdir(parents=True)
    (vendored / "go.mod").write_text("module dep\n")

    roots = find_go_roots(tmp_path)
    assert tmp_path in roots
    assert sub in roots
    assert vendored not in roots


def test_find_go_roots_none(tmp_path):
    assert find_go_roots(tmp_path) == []


def test_detect_project_name_from_module(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/foo/bar\n")
    assert detect_project_name(tmp_path) == "bar"


def test_detect_project_name_fallback(tmp_path):
    proj = tmp_path / "myproj"
    proj.mkdir()
    assert detect_project_name(proj) == "myproj"
