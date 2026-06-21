"""Tests for Rust dependency parsing and import classification."""

from graphlens_rust._deps import (
    CargoTomlParser,
    classify_rust_import,
    read_crate_name,
)


def test_can_parse(tmp_path):
    assert not CargoTomlParser().can_parse(tmp_path)
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\n')
    assert CargoTomlParser().can_parse(tmp_path)


def test_parse_dependencies_normalizes_hyphens(tmp_path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname="x"\n'
        '[dependencies]\nserde = "1"\ntokio-util = "0.7"\n'
        '[dev-dependencies]\npretty_assertions = "1"\n'
    )
    deps = CargoTomlParser().parse(tmp_path)
    assert "serde" in deps
    assert "tokio_util" in deps
    assert "pretty_assertions" in deps


def test_parse_missing_returns_empty(tmp_path):
    assert CargoTomlParser().parse(tmp_path) == frozenset()


def test_parse_invalid_toml_returns_empty(tmp_path):
    (tmp_path / "Cargo.toml").write_text("not = valid = toml ===")
    assert CargoTomlParser().parse(tmp_path) == frozenset()


def test_read_crate_name(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="mycrate"\n')
    assert read_crate_name(tmp_path) == "mycrate"


def test_read_crate_name_no_package(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers=["a"]\n')
    assert read_crate_name(tmp_path) is None


def test_read_crate_name_no_file(tmp_path):
    assert read_crate_name(tmp_path) is None


def test_classify_rust_import():
    assert classify_rust_import("std::fmt", "demo", []) == "stdlib"
    assert classify_rust_import("crate::util", "demo", []) == "internal"
    assert classify_rust_import("self::x", "demo", []) == "internal"
    assert classify_rust_import("demo::x", "demo", []) == "internal"
    assert (
        classify_rust_import("serde::Serialize", "demo", ["serde"])
        == "third_party"
    )
    assert classify_rust_import("whoknows::x", "demo", []) == "unknown"
