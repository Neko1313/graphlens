from pathlib import Path

from graphlens_php._project_detector import (
    detect_project_name,
    find_php_roots,
    is_php_project,
)


def test_is_php_project_via_composer(tmp_path: Path):
    (tmp_path / "composer.json").write_text("{}")
    assert is_php_project(tmp_path) is True


def test_is_php_project_via_php_fallback(tmp_path: Path):
    (tmp_path / "index.php").write_text("<?php echo 1;")
    assert is_php_project(tmp_path) is True


def test_is_php_project_false(tmp_path: Path):
    (tmp_path / "readme.md").write_text("hi")
    assert is_php_project(tmp_path) is False


def test_find_php_roots_single(tmp_path: Path):
    (tmp_path / "composer.json").write_text("{}")
    roots = find_php_roots(tmp_path)
    assert roots == [tmp_path]


def test_find_php_roots_monorepo(tmp_path: Path):
    (tmp_path / "composer.json").write_text("{}")
    (tmp_path / "pkg-a").mkdir()
    (tmp_path / "pkg-a" / "composer.json").write_text("{}")
    (tmp_path / "pkg-b").mkdir()
    (tmp_path / "pkg-b" / "composer.json").write_text("{}")
    roots = find_php_roots(tmp_path)
    assert tmp_path in roots
    assert tmp_path / "pkg-a" in roots
    assert tmp_path / "pkg-b" in roots


def test_find_php_roots_excludes_vendor(tmp_path: Path):
    (tmp_path / "composer.json").write_text("{}")
    vendored = tmp_path / "vendor" / "acme" / "lib"
    vendored.mkdir(parents=True)
    (vendored / "composer.json").write_text("{}")
    roots = find_php_roots(tmp_path)
    assert vendored not in roots


def test_find_php_roots_fallback_no_marker(tmp_path: Path):
    (tmp_path / "a.php").write_text("<?php")
    assert find_php_roots(tmp_path) == [tmp_path]


def test_detect_project_name_from_composer(tmp_path: Path):
    (tmp_path / "composer.json").write_text('{"name": "acme/demo"}')
    assert detect_project_name(tmp_path) == "acme/demo"


def test_detect_project_name_no_name_key(tmp_path: Path):
    (tmp_path / "composer.json").write_text('{"type": "library"}')
    assert detect_project_name(tmp_path) == tmp_path.name


def test_detect_project_name_invalid_json(tmp_path: Path):
    (tmp_path / "composer.json").write_text("{not json")
    assert detect_project_name(tmp_path) == tmp_path.name


def test_detect_project_name_no_composer(tmp_path: Path):
    assert detect_project_name(tmp_path) == tmp_path.name
