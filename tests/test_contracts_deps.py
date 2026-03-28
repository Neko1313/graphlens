"""Tests for normalize_pkg_name and DependencyFileParser contract."""

import pytest

from code_graph import DependencyFileParser, normalize_pkg_name


class TestNormalizePkgName:
    def test_simple(self) -> None:
        assert normalize_pkg_name("requests") == "requests"

    def test_uppercase(self) -> None:
        assert normalize_pkg_name("Flask") == "flask"
        assert normalize_pkg_name("NUMPY") == "numpy"

    def test_hyphen_to_underscore(self) -> None:
        assert normalize_pkg_name("scikit-learn") == "scikit_learn"
        assert normalize_pkg_name("my-cool-pkg") == "my_cool_pkg"

    def test_version_specifier_gt(self) -> None:
        assert normalize_pkg_name("requests>=2.0") == "requests"

    def test_version_specifier_lt(self) -> None:
        assert normalize_pkg_name("requests<3.0") == "requests"

    def test_version_specifier_eq(self) -> None:
        assert normalize_pkg_name("requests==2.28.0") == "requests"

    def test_version_specifier_ne(self) -> None:
        assert normalize_pkg_name("requests!=2.0") == "requests"

    def test_version_specifier_compatible(self) -> None:
        assert normalize_pkg_name("requests~=2.28") == "requests"

    def test_extras(self) -> None:
        assert normalize_pkg_name("requests[security]") == "requests"

    def test_extras_and_version(self) -> None:
        assert normalize_pkg_name("requests[security]>=2.0") == "requests"

    def test_inline_comment(self) -> None:
        assert normalize_pkg_name("requests  # http client") == "requests"

    def test_env_marker(self) -> None:
        assert normalize_pkg_name("requests; python_version>='3.8'") == "requests"

    def test_space_separator(self) -> None:
        assert normalize_pkg_name("requests 2.0") == "requests"

    def test_empty_string(self) -> None:
        assert normalize_pkg_name("") == ""

    def test_whitespace_only(self) -> None:
        assert normalize_pkg_name("   ") == ""

    def test_scoped_npm_package(self) -> None:
        result = normalize_pkg_name("@types/node")
        assert result == "@types/node"

    def test_scoped_npm_uppercase(self) -> None:
        result = normalize_pkg_name("@Types/Node")
        assert result == "@types/node"

    def test_complex(self) -> None:
        assert normalize_pkg_name("Foo[bar]>=1.0 ; python_version>='3.8'") == "foo"

    def test_comment_only(self) -> None:
        assert normalize_pkg_name("# just a comment") == ""


class TestDependencyFileParserABC:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            DependencyFileParser()  # type: ignore[abstract]

    def test_concrete_subclass_works(self) -> None:
        from pathlib import Path

        class AlwaysEmptyParser(DependencyFileParser):
            def can_parse(self, project_root: Path) -> bool:
                return True

            def parse(self, project_root: Path) -> frozenset[str]:
                return frozenset()

        p = AlwaysEmptyParser()
        assert p.can_parse(Path("."))
        assert p.parse(Path(".")) == frozenset()
