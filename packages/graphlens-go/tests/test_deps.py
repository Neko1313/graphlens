"""Tests for Go dependency parsing and import classification."""

from graphlens_go._deps import (
    GoModParser,
    classify_go_import,
    read_module_path,
)


def test_can_parse(tmp_path):
    assert not GoModParser().can_parse(tmp_path)
    (tmp_path / "go.mod").write_text("module x\n")
    assert GoModParser().can_parse(tmp_path)


def test_parse_require_block(tmp_path):
    (tmp_path / "go.mod").write_text(
        "module x\nrequire (\n"
        "\tgithub.com/a/b v1.0.0\n"
        "\t// a comment\n"
        "\tgithub.com/c/d v2.0.0\n)\n"
    )
    deps = GoModParser().parse(tmp_path)
    assert "github.com/a/b" in deps
    assert "github.com/c/d" in deps


def test_parse_require_single_line(tmp_path):
    (tmp_path / "go.mod").write_text(
        "module x\nrequire github.com/a/b v1.0.0\n"
    )
    assert "github.com/a/b" in GoModParser().parse(tmp_path)


def test_parse_single_line_then_block_no_paren(tmp_path):
    # A single-line require followed by a require(...) block must not capture
    # the block opener's "(" as a module path.
    (tmp_path / "go.mod").write_text(
        "module x\n"
        "require golang.org/x/sync v0.1.0\n"
        "require (\n\tgithub.com/a/b v1.0.0\n)\n"
    )
    deps = GoModParser().parse(tmp_path)
    assert "golang.org/x/sync" in deps
    assert "github.com/a/b" in deps
    assert "(" not in deps


def test_parse_missing_file_returns_empty(tmp_path):
    assert GoModParser().parse(tmp_path) == frozenset()


def test_read_module_path(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/foo\n")
    assert read_module_path(tmp_path) == "example.com/foo"


def test_read_module_path_missing(tmp_path):
    assert read_module_path(tmp_path) is None


def test_classify_go_import():
    assert classify_go_import("fmt", "example.com/x", []) == "stdlib"
    assert (
        classify_go_import("net/http", "example.com/x", []) == "stdlib"
    )
    assert (
        classify_go_import("example.com/x", "example.com/x", [])
        == "internal"
    )
    assert (
        classify_go_import("example.com/x/util", "example.com/x", [])
        == "internal"
    )
    assert (
        classify_go_import(
            "github.com/a/b", "example.com/x", ["github.com/a/b"]
        )
        == "third_party"
    )
    assert (
        classify_go_import("github.com/z/z", "example.com/x", [])
        == "unknown"
    )
