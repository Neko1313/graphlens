"""Tests for TypeScript dependency file parsers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphlens_typescript._deps import (
    TYPESCRIPT_DEFAULT_DEP_PARSERS,
    PackageJsonParser,
    get_stdlib_names,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestPackageJsonParser:
    def setup_method(self):
        self.parser = PackageJsonParser()

    def test_can_parse_with_package_json(self, tmp_path: Path):
        (tmp_path / "package.json").touch()
        assert self.parser.can_parse(tmp_path)

    def test_cannot_parse_without_package_json(self, tmp_path: Path):
        assert not self.parser.can_parse(tmp_path)

    def test_parses_dependencies(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            '{"dependencies": {"lodash": "^4.0.0", "express": "^4.18.0"}}'
        )
        result = self.parser.parse(tmp_path)
        assert "lodash" in result
        assert "express" in result

    def test_parses_dev_dependencies(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            '{"devDependencies": {"jest": "^29.0.0", "typescript": "^5.0.0"}}'
        )
        result = self.parser.parse(tmp_path)
        assert "jest" in result
        assert "typescript" in result

    def test_parses_peer_dependencies(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            '{"peerDependencies": {"react": "^18.0.0"}}'
        )
        result = self.parser.parse(tmp_path)
        assert "react" in result

    def test_parses_optional_dependencies(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            '{"optionalDependencies": {"fsevents": "^2.0.0"}}'
        )
        result = self.parser.parse(tmp_path)
        assert "fsevents" in result

    def test_normalizes_scoped_packages(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            '{"dependencies": {"@types/node": "^20.0.0"}}'
        )
        result = self.parser.parse(tmp_path)
        assert "@types/node" in result

    def test_returns_frozenset_on_invalid_json(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("not json")
        result = self.parser.parse(tmp_path)
        assert result == frozenset()

    def test_returns_frozenset_on_missing_file(self, tmp_path: Path):
        result = self.parser.parse(tmp_path)
        assert result == frozenset()

    def test_empty_package_json(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{}")
        result = self.parser.parse(tmp_path)
        assert result == frozenset()


class TestGetStdlibNames:
    def test_returns_frozenset(self):
        result = get_stdlib_names()
        assert isinstance(result, frozenset)

    def test_contains_common_node_builtins(self):
        result = get_stdlib_names()
        for mod in ("fs", "path", "os", "crypto", "http", "https", "events"):
            assert mod in result, f"Expected '{mod}' in stdlib names"

    def test_does_not_contain_third_party(self):
        result = get_stdlib_names()
        for pkg in ("lodash", "express", "react", "axios"):
            assert pkg not in result


class TestDefaultDepParsers:
    def test_default_parsers_list(self):
        assert len(TYPESCRIPT_DEFAULT_DEP_PARSERS) >= 1

    def test_package_json_parser_in_defaults(self):
        parsers = TYPESCRIPT_DEFAULT_DEP_PARSERS
        assert any(isinstance(p, PackageJsonParser) for p in parsers)
