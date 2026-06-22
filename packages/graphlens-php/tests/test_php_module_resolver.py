import json
from pathlib import Path

from graphlens_php._module_resolver import (
    find_source_roots,
    internal_namespace_tops,
    load_psr4_map,
    path_to_namespace,
)


def _composer(tmp_path: Path, data: dict) -> Path:
    (tmp_path / "composer.json").write_text(json.dumps(data))
    return tmp_path


def test_load_psr4_map_basic(tmp_path: Path):
    _composer(tmp_path, {"autoload": {"psr-4": {"App\\": "src/"}}})
    psr4 = load_psr4_map(tmp_path)
    assert "App" in psr4
    assert psr4["App"] == [tmp_path / "src/"]


def test_load_psr4_map_string_and_list_dirs(tmp_path: Path):
    _composer(
        tmp_path,
        {"autoload": {"psr-4": {"App\\": ["src/", "lib/"]}}},
    )
    psr4 = load_psr4_map(tmp_path)
    assert psr4["App"] == [tmp_path / "src/", tmp_path / "lib/"]


def test_load_psr4_map_includes_autoload_dev(tmp_path: Path):
    _composer(
        tmp_path,
        {
            "autoload": {"psr-4": {"App\\": "src/"}},
            "autoload-dev": {"psr-4": {"App\\Tests\\": "tests/"}},
        },
    )
    psr4 = load_psr4_map(tmp_path)
    assert "App" in psr4
    assert "App\\Tests" in psr4


def test_load_psr4_map_missing_composer(tmp_path: Path):
    assert load_psr4_map(tmp_path) == {}


def test_load_psr4_map_invalid_json(tmp_path: Path):
    (tmp_path / "composer.json").write_text("{bad")
    assert load_psr4_map(tmp_path) == {}


def test_load_psr4_map_non_dict_root(tmp_path: Path):
    (tmp_path / "composer.json").write_text("[]")
    assert load_psr4_map(tmp_path) == {}


def test_load_psr4_map_non_dict_autoload(tmp_path: Path):
    _composer(tmp_path, {"autoload": "nope"})
    assert load_psr4_map(tmp_path) == {}


def test_load_psr4_map_non_dict_psr4(tmp_path: Path):
    _composer(tmp_path, {"autoload": {"psr-4": "nope"}})
    assert load_psr4_map(tmp_path) == {}


def test_load_psr4_map_skips_non_string_prefix_and_dirs(tmp_path: Path):
    _composer(
        tmp_path,
        {"autoload": {"psr-4": {"App\\": {"weird": 1}}}},
    )
    # dirs is a dict (not str/list) → namespace skipped entirely
    assert load_psr4_map(tmp_path) == {}


def test_internal_namespace_tops(tmp_path: Path):
    _composer(
        tmp_path,
        {"autoload": {"psr-4": {"App\\Service\\": "src/", "Acme\\": "x/"}}},
    )
    assert internal_namespace_tops(tmp_path) == {"App", "Acme"}


def test_internal_namespace_tops_empty_prefix_ignored(tmp_path: Path):
    _composer(tmp_path, {"autoload": {"psr-4": {"\\": "src/"}}})
    assert internal_namespace_tops(tmp_path) == set()


def test_find_source_roots(tmp_path: Path):
    (tmp_path / "src").mkdir()
    _composer(tmp_path, {"autoload": {"psr-4": {"App\\": "src/"}}})
    roots = find_source_roots(tmp_path, [])
    assert (tmp_path / "src") in roots
    assert tmp_path == roots[-1]


def test_find_source_roots_skips_missing_dir(tmp_path: Path):
    _composer(tmp_path, {"autoload": {"psr-4": {"App\\": "nope/"}}})
    roots = find_source_roots(tmp_path, [])
    assert roots == [tmp_path]


def test_find_source_roots_root_dir_not_duplicated(tmp_path: Path):
    # psr-4 mapping to "." resolves to project_root itself.
    _composer(tmp_path, {"autoload": {"psr-4": {"App\\": "."}}})
    roots = find_source_roots(tmp_path, [])
    assert roots == [tmp_path]


def test_path_to_namespace_prefers_longest_prefix(tmp_path: Path):
    (tmp_path / "src" / "Sub").mkdir(parents=True)
    # Longer prefix is listed first so the shorter one hits the depth guard.
    _composer(
        tmp_path,
        {"autoload": {"psr-4": {"App\\Sub\\": "src/Sub/", "App\\": "src/"}}},
    )
    file = tmp_path / "src" / "Sub" / "Foo.php"
    assert path_to_namespace(file, tmp_path) == "App\\Sub"


def test_path_to_namespace_match(tmp_path: Path):
    (tmp_path / "src" / "Service").mkdir(parents=True)
    _composer(tmp_path, {"autoload": {"psr-4": {"App\\": "src/"}}})
    file = tmp_path / "src" / "Service" / "Foo.php"
    assert path_to_namespace(file, tmp_path) == "App\\Service"


def test_path_to_namespace_root_of_prefix(tmp_path: Path):
    (tmp_path / "src").mkdir()
    _composer(tmp_path, {"autoload": {"psr-4": {"App\\": "src/"}}})
    file = tmp_path / "src" / "Foo.php"
    assert path_to_namespace(file, tmp_path) == "App"


def test_path_to_namespace_no_match(tmp_path: Path):
    _composer(tmp_path, {"autoload": {"psr-4": {"App\\": "src/"}}})
    file = tmp_path / "other" / "Foo.php"
    assert path_to_namespace(file, tmp_path) == ""
