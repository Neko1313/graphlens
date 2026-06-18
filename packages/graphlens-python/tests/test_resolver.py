from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from graphlens_python._resolver import JediResolver


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "util.py").write_text(
        "def helper(x):\n    return x\n"
    )
    (tmp_path / "pkg" / "main.py").write_text(
        "from pkg.util import helper\n"
        "import os\n"
        "\n"
        "def run():\n"
        "    helper(1)\n"
        "    os.getcwd()\n"
    )
    return tmp_path


@pytest.fixture
def proj_with_class(tmp_path: Path) -> Path:
    """Project with a class in models.py instantiated in app.py."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "models.py").write_text("class User:\n    pass\n")
    (tmp_path / "pkg" / "app.py").write_text(
        "from pkg.models import User\n"
        "\n"
        "u = User()\n"
    )
    return tmp_path


def _resolver(proj: Path) -> JediResolver:
    r = JediResolver(stdlib_names=frozenset({"os"}))
    r.prepare(proj, list(proj.rglob("*.py")))
    return r


def test_resolves_imported_call_to_internal_definition(proj):
    r = _resolver(proj)
    # 'helper' callee at main.py line 5, col 5 (1-based)
    ref = r.definition_at(proj / "pkg" / "main.py", 5, 5)
    assert ref is not None
    assert ref.full_name.endswith("util.helper")
    assert ref.file_path == proj / "pkg" / "util.py"
    assert ref.origin == "internal"
    assert ref.line == 1  # def helper on line 1
    assert ref.col == 5   # name 'helper' is 1-based col 5


def test_classifies_stdlib_origin(proj):
    r = _resolver(proj)
    # 'os' at main.py line 6 col 5
    ref = r.definition_at(proj / "pkg" / "main.py", 6, 5)
    assert ref is not None
    assert ref.origin == "stdlib"


def test_missing_resolution_returns_none(proj):
    r = _resolver(proj)
    ref = r.definition_at(proj / "pkg" / "main.py", 99, 99)
    assert ref is None


def test_infer_type_at_class_instance(proj_with_class):
    """infer_type_at on a class instance variable returns the class type."""
    r = JediResolver(stdlib_names=frozenset())
    r.prepare(proj_with_class, list(proj_with_class.rglob("*.py")))

    # app.py line 3: "u = User()" — infer type of 'u' at col 1 (1-based)
    # jedi infers the instance type as pkg.models.User
    ref = r.infer_type_at(proj_with_class / "pkg" / "app.py", 3, 1)

    assert ref is not None
    assert ref.full_name.endswith("User")
    # jedi returns 'instance' for an inferred variable holding a class instance
    assert ref.kind == "instance"
    assert ref.origin == "internal"
    assert ref.col >= 1


def test_references_to_returns_occurrences_with_definition(proj):
    """references_to on a function definition returns all call sites."""
    r = _resolver(proj)

    # util.py line 1, col 5 (1-based) points to 'helper' in "def helper(x):"
    # (col 5 → 0-based col 4, which is the 'h' of 'helper')
    occs = r.references_to(proj / "pkg" / "util.py", 1, 5)

    assert len(occs) > 0
    # At least one occurrence must be marked as a definition
    assert any(o.is_definition for o in occs)
    # All occurrences must have 1-based coordinates
    for o in occs:
        assert o.line >= 1
        assert o.col >= 1


# --- Error-handling / edge-case coverage ---


def test_prepare_jedi_project_failure_sets_project_none(tmp_path):
    """prepare() swallows jedi.Project exceptions and leaves _project as None."""
    r = JediResolver(stdlib_names=frozenset())
    with patch("graphlens_python._resolver.jedi.Project", side_effect=RuntimeError("boom")):
        r.prepare(tmp_path, [])
    assert r._project is None


def test_definition_at_returns_none_when_project_not_prepared(tmp_path):
    """definition_at returns None when prepare() was never called (_project is None)."""
    r = JediResolver(stdlib_names=frozenset())
    # _project starts as None, so _script returns None
    assert r.definition_at(tmp_path / "x.py", 1, 1) is None


def test_infer_type_at_returns_none_when_project_not_prepared(tmp_path):
    """infer_type_at returns None when _project is None."""
    r = JediResolver(stdlib_names=frozenset())
    assert r.infer_type_at(tmp_path / "x.py", 1, 1) is None


def test_references_to_returns_empty_when_project_not_prepared(tmp_path):
    """references_to returns [] when _project is None."""
    r = JediResolver(stdlib_names=frozenset())
    assert r.references_to(tmp_path / "x.py", 1, 1) == []


def test_script_creation_failure_returns_none(tmp_path, proj):
    """_script() swallows jedi.Script exceptions and returns None, causing
    definition_at/infer_type_at/references_to to return None/[]."""
    r = JediResolver(stdlib_names=frozenset())
    r.prepare(proj, list(proj.rglob("*.py")))
    with patch("graphlens_python._resolver.jedi.Script", side_effect=RuntimeError("bad")):
        assert r.definition_at(proj / "pkg" / "main.py", 1, 1) is None
        assert r.infer_type_at(proj / "pkg" / "main.py", 1, 1) is None
        assert r.references_to(proj / "pkg" / "main.py", 1, 1) == []


def test_definition_at_swallows_goto_exception(proj):
    """definition_at returns None when jedi.Script.goto raises."""
    r = _resolver(proj)
    script_mock = MagicMock()
    script_mock.goto.side_effect = RuntimeError("goto failed")
    with patch.object(r, "_script", return_value=script_mock):
        assert r.definition_at(proj / "pkg" / "main.py", 1, 1) is None


def test_infer_type_at_swallows_infer_exception(proj):
    """infer_type_at returns None when jedi.Script.infer raises."""
    r = _resolver(proj)
    script_mock = MagicMock()
    script_mock.infer.side_effect = RuntimeError("infer failed")
    with patch.object(r, "_script", return_value=script_mock):
        assert r.infer_type_at(proj / "pkg" / "main.py", 1, 1) is None


def test_references_to_swallows_get_references_exception(proj):
    """references_to returns [] when jedi.Script.get_references raises."""
    r = _resolver(proj)
    script_mock = MagicMock()
    script_mock.get_references.side_effect = RuntimeError("refs failed")
    with patch.object(r, "_script", return_value=script_mock):
        assert r.references_to(proj / "pkg" / "main.py", 1, 1) == []


def test_references_to_skips_occurrences_with_no_module_path(proj):
    """references_to skips Name entries where module_path or line is None."""
    r = _resolver(proj)
    name_no_path = MagicMock()
    name_no_path.module_path = None
    name_no_path.line = 1
    name_with_path = MagicMock()
    name_with_path.module_path = proj / "pkg" / "util.py"
    name_with_path.line = 1
    name_with_path.column = 0
    name_with_path.is_definition.return_value = True
    script_mock = MagicMock()
    script_mock.get_references.return_value = [name_no_path, name_with_path]
    with patch.object(r, "_script", return_value=script_mock):
        occs = r.references_to(proj / "pkg" / "main.py", 1, 1)
    # Only the one with a module_path should appear
    assert len(occs) == 1
    assert occs[0].col == 1


def test_classify_stdlib_when_module_path_none(proj):
    """_classify returns 'stdlib' when module_path is None."""
    r = _resolver(proj)
    assert r._classify(None, "os.path", False) == "stdlib"


def test_classify_third_party_via_site_packages(proj):
    """_classify returns 'third_party' for paths containing site-packages."""
    r = _resolver(proj)
    # Use /usr/lib path — guaranteed to be outside tmp proj root
    fake_path = Path("/usr/lib/python3/site-packages/requests/api.py")
    assert r._classify(fake_path, "requests.api", False) == "third_party"


def test_classify_unknown_for_unrecognised_external(proj):
    """_classify returns 'unknown' for external paths not in site-packages or stdlib."""
    r = _resolver(proj)
    # Use /opt — guaranteed to be outside tmp proj root and not site-packages
    fake_path = Path("/opt/exotic/module.py")
    # top-level name "exotic" is not in stdlib_names (only "os" is)
    assert r._classify(fake_path, "exotic.module", False) == "unknown"
