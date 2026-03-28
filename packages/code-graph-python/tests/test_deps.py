"""Tests for Python dependency file parsers."""

from pathlib import Path

from code_graph_python._deps import (
    PYTHON_DEFAULT_DEP_PARSERS,
    PyprojectDepsParser,
    RequirementsTxtParser,
    SetupCfgDepsParser,
    get_stdlib_names,
)


class TestPyprojectDepsParser:
    def setup_method(self):
        self.parser = PyprojectDepsParser()

    def test_can_parse_with_pyproject(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").touch()
        assert self.parser.can_parse(tmp_path)

    def test_cannot_parse_without_pyproject(self, tmp_path: Path):
        assert not self.parser.can_parse(tmp_path)

    def test_pep621_dependencies(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["requests>=2.0", "flask"]\n'
        )
        result = self.parser.parse(tmp_path)
        assert "requests" in result
        assert "flask" in result

    def test_pep621_optional_dependencies(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\n'
            '[project.optional-dependencies]\n'
            'test = ["pytest", "pytest-cov"]\n'
        )
        result = self.parser.parse(tmp_path)
        assert "pytest" in result
        assert "pytest_cov" in result

    def test_poetry_dependencies(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.poetry.dependencies]\n"
            'python = "^3.11"\n'
            'requests = "^2.0"\n'
        )
        result = self.parser.parse(tmp_path)
        assert "requests" in result
        assert "python" not in result  # filtered out

    def test_poetry_dev_dependencies(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.poetry.dev-dependencies]\n"
            'pytest = "*"\n'
        )
        result = self.parser.parse(tmp_path)
        assert "pytest" in result

    def test_poetry_groups(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.poetry.group.dev.dependencies]\n"
            'black = "*"\n'
        )
        result = self.parser.parse(tmp_path)
        assert "black" in result

    def test_normalizes_hyphens(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["scikit-learn"]\n'
        )
        result = self.parser.parse(tmp_path)
        assert "scikit_learn" in result

    def test_invalid_toml_returns_empty(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[[invalid toml\n", encoding="utf-8")
        assert self.parser.parse(tmp_path) == frozenset()

    def test_read_error_returns_empty(self, tmp_path: Path):
        path = tmp_path / "pyproject.toml"
        path.touch()
        path.chmod(0o000)
        try:
            result = self.parser.parse(tmp_path)
            assert result == frozenset()
        finally:
            path.chmod(0o644)

    def test_empty_sections_returns_empty(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[build-system]\nrequires = []\n")
        assert self.parser.parse(tmp_path) == frozenset()


class TestRequirementsTxtParser:
    def setup_method(self):
        self.parser = RequirementsTxtParser()

    def test_can_parse_with_requirements(self, tmp_path: Path):
        (tmp_path / "requirements.txt").touch()
        assert self.parser.can_parse(tmp_path)

    def test_cannot_parse_without_requirements(self, tmp_path: Path):
        assert not self.parser.can_parse(tmp_path)

    def test_simple_packages(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("requests\nflask\n")
        result = self.parser.parse(tmp_path)
        assert "requests" in result
        assert "flask" in result

    def test_version_specifiers(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("requests>=2.0\nflask==2.3.0\n")
        result = self.parser.parse(tmp_path)
        assert "requests" in result
        assert "flask" in result

    def test_comments_ignored(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("# a comment\nrequests\n")
        result = self.parser.parse(tmp_path)
        assert "requests" in result
        assert len(result) == 1

    def test_blank_lines_ignored(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("\nrequests\n\nflask\n\n")
        result = self.parser.parse(tmp_path)
        assert len(result) == 2

    def test_vcs_lines_skipped(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text(
            "git+https://github.com/org/repo.git\nhttps://example.com/pkg.whl\n"
        )
        assert self.parser.parse(tmp_path) == frozenset()

    def test_editable_install_skipped(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("-e .\n")
        assert self.parser.parse(tmp_path) == frozenset()

    def test_recursive_include(self, tmp_path: Path):
        (tmp_path / "requirements-base.txt").write_text("requests\n")
        (tmp_path / "requirements.txt").write_text("-r requirements-base.txt\nflask\n")
        result = self.parser.parse(tmp_path)
        assert "requests" in result
        assert "flask" in result

    def test_recursive_include_nonexistent_file(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("-r nonexistent.txt\nflask\n")
        result = self.parser.parse(tmp_path)
        assert "flask" in result  # still parses remaining lines

    def test_read_error_silently_skipped(self, tmp_path: Path):
        path = tmp_path / "requirements.txt"
        path.touch()
        path.chmod(0o000)
        try:
            result = self.parser.parse(tmp_path)
            assert result == frozenset()
        finally:
            path.chmod(0o644)

    def test_multiple_requirements_files(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("requests\n")
        (tmp_path / "requirements-dev.txt").write_text("pytest\n")
        result = self.parser.parse(tmp_path)
        assert "requests" in result
        assert "pytest" in result

    def test_constraint_line_skipped(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("-c constraints.txt\nrequests\n")
        result = self.parser.parse(tmp_path)
        assert "requests" in result


class TestSetupCfgDepsParser:
    def setup_method(self):
        self.parser = SetupCfgDepsParser()

    def test_can_parse_with_setup_cfg(self, tmp_path: Path):
        (tmp_path / "setup.cfg").touch()
        assert self.parser.can_parse(tmp_path)

    def test_cannot_parse_without_setup_cfg(self, tmp_path: Path):
        assert not self.parser.can_parse(tmp_path)

    def test_install_requires(self, tmp_path: Path):
        (tmp_path / "setup.cfg").write_text(
            "[options]\ninstall_requires =\n    requests>=2.0\n    flask\n"
        )
        result = self.parser.parse(tmp_path)
        assert "requests" in result
        assert "flask" in result

    def test_empty_install_requires(self, tmp_path: Path):
        (tmp_path / "setup.cfg").write_text("[options]\n")
        assert self.parser.parse(tmp_path) == frozenset()

    def test_invalid_config_raises_configparser_error(self, tmp_path: Path):
        """A setup.cfg with no section header raises MissingSectionHeaderError."""
        (tmp_path / "setup.cfg").write_text("no_section_key = value\n")
        assert self.parser.parse(tmp_path) == frozenset()

    def test_extras_require_section_with_install_requires(self, tmp_path: Path):
        """options.extras_require section with an install_requires key."""
        (tmp_path / "setup.cfg").write_text(
            "[options.extras_require]\ninstall_requires = extra-pkg\n"
        )
        result = self.parser.parse(tmp_path)
        # The key "install_requires" inside options.extras_require is picked up
        assert "extra_pkg" in result

    def test_extras_require_section_standard(self, tmp_path: Path):
        # Standard extras_require section (dev = ...) — install_requires fallback "" → empty
        (tmp_path / "setup.cfg").write_text(
            "[options.extras_require]\ndev = pytest\n    black\n"
        )
        result = self.parser.parse(tmp_path)
        assert isinstance(result, frozenset)


class TestGetStdlibNames:
    def test_returns_frozenset(self):
        result = get_stdlib_names()
        assert isinstance(result, frozenset)

    def test_contains_common_modules(self):
        stdlib = get_stdlib_names()
        for module in ("os", "sys", "json", "re", "pathlib", "abc", "asyncio"):
            assert module in stdlib, f"Expected {module} in stdlib"

    def test_not_empty(self):
        assert len(get_stdlib_names()) > 0


class TestDefaultDepParsers:
    def test_default_list_non_empty(self):
        assert len(PYTHON_DEFAULT_DEP_PARSERS) > 0

    def test_includes_all_parser_types(self):
        types = {type(p) for p in PYTHON_DEFAULT_DEP_PARSERS}
        assert PyprojectDepsParser in types
        assert RequirementsTxtParser in types
        assert SetupCfgDepsParser in types
