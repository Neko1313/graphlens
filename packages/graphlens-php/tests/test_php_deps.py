import json
from pathlib import Path

from graphlens_php._deps import (
    ComposerJsonDepsParser,
    ComposerLockDepsParser,
    _vendor_prefix,
    get_stdlib_names,
)


def test_vendor_prefix():
    assert _vendor_prefix("symfony/console") == "symfony"
    assert _vendor_prefix("Monolog/Monolog") == "monolog"
    assert _vendor_prefix("php") == ""
    assert _vendor_prefix(123) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# composer.json
# ---------------------------------------------------------------------------


def test_composer_json_can_parse(tmp_path: Path):
    parser = ComposerJsonDepsParser()
    assert parser.can_parse(tmp_path) is False
    (tmp_path / "composer.json").write_text("{}")
    assert parser.can_parse(tmp_path) is True


def test_composer_json_require_and_dev(tmp_path: Path):
    (tmp_path / "composer.json").write_text(
        json.dumps({
            "require": {
                "php": ">=8.1",
                "ext-json": "*",
                "symfony/console": "^6",
                "monolog/monolog": "^3",
            },
            "require-dev": {"phpunit/phpunit": "^10"},
        })
    )
    names = ComposerJsonDepsParser().parse(tmp_path)
    assert names == frozenset({"symfony", "monolog", "phpunit"})


def test_composer_json_invalid(tmp_path: Path):
    (tmp_path / "composer.json").write_text("{nope")
    assert ComposerJsonDepsParser().parse(tmp_path) == frozenset()


def test_composer_json_non_dict(tmp_path: Path):
    (tmp_path / "composer.json").write_text("[]")
    assert ComposerJsonDepsParser().parse(tmp_path) == frozenset()


def test_composer_json_non_dict_section(tmp_path: Path):
    (tmp_path / "composer.json").write_text(json.dumps({"require": "nope"}))
    assert ComposerJsonDepsParser().parse(tmp_path) == frozenset()


def test_composer_json_skips_slashless_package(tmp_path: Path):
    # A require key without a vendor "/" yields no vendor prefix.
    (tmp_path / "composer.json").write_text(
        json.dumps({"require": {"slashless": "*", "acme/lib": "^1"}})
    )
    assert ComposerJsonDepsParser().parse(tmp_path) == frozenset({"acme"})


# ---------------------------------------------------------------------------
# composer.lock
# ---------------------------------------------------------------------------


def test_composer_lock_can_parse(tmp_path: Path):
    parser = ComposerLockDepsParser()
    assert parser.can_parse(tmp_path) is False
    (tmp_path / "composer.lock").write_text("{}")
    assert parser.can_parse(tmp_path) is True


def test_composer_lock_packages(tmp_path: Path):
    (tmp_path / "composer.lock").write_text(
        json.dumps({
            "packages": [
                {"name": "guzzlehttp/guzzle"},
                {"name": "psr/log"},
                "not-a-dict",
            ],
            "packages-dev": [{"name": "mockery/mockery"}],
        })
    )
    names = ComposerLockDepsParser().parse(tmp_path)
    assert names == frozenset({"guzzlehttp", "psr", "mockery"})


def test_composer_lock_invalid(tmp_path: Path):
    (tmp_path / "composer.lock").write_text("{bad")
    assert ComposerLockDepsParser().parse(tmp_path) == frozenset()


def test_composer_lock_non_dict(tmp_path: Path):
    (tmp_path / "composer.lock").write_text("[]")
    assert ComposerLockDepsParser().parse(tmp_path) == frozenset()


def test_composer_lock_non_list_section(tmp_path: Path):
    (tmp_path / "composer.lock").write_text(json.dumps({"packages": {}}))
    assert ComposerLockDepsParser().parse(tmp_path) == frozenset()


def test_composer_lock_skips_slashless_name(tmp_path: Path):
    (tmp_path / "composer.lock").write_text(
        json.dumps({"packages": [{"name": "slashless"}, {"name": "a/b"}]})
    )
    assert ComposerLockDepsParser().parse(tmp_path) == frozenset({"a"})


def test_get_stdlib_names():
    names = get_stdlib_names()
    assert "DateTime" in names
    assert "Exception" in names
    assert "PDO" in names
