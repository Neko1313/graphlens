from pathlib import Path

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
