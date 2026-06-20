"""Tests for Rust project detection."""

from graphlens_rust._project_detector import (
    detect_project_name,
    find_rust_roots,
    is_rust_project,
)


def test_is_rust_project(tmp_path):
    assert not is_rust_project(tmp_path)
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\n')
    assert is_rust_project(tmp_path)


def test_find_rust_roots_excludes_target(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="root"\n')
    sub = tmp_path / "crates" / "a"
    sub.mkdir(parents=True)
    (sub / "Cargo.toml").write_text('[package]\nname="a"\n')
    built = tmp_path / "target" / "dep"
    built.mkdir(parents=True)
    (built / "Cargo.toml").write_text('[package]\nname="dep"\n')

    roots = find_rust_roots(tmp_path)
    assert tmp_path in roots
    assert sub in roots
    assert built not in roots


def test_find_rust_roots_none(tmp_path):
    assert find_rust_roots(tmp_path) == []


def test_detect_project_name_from_cargo(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="mycrate"\n')
    assert detect_project_name(tmp_path) == "mycrate"


def test_detect_project_name_fallback(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    assert detect_project_name(proj) == "proj"
