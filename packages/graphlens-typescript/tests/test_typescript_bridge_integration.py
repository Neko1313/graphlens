"""End-to-end integration test: real Node + typescript Compiler API bridge.

Gated with ``@pytest.mark.skipif`` so the suite is skipped when ``node`` is
not available.  Not included in Python-side coverage (end-to-end only).
"""

import shutil

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def test_bridge_resolves_internal_import(tmp_path):
    """Resolver returns the declaration in util.ts for a call in main.ts."""
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"strict":false}}'
    )
    (tmp_path / "util.ts").write_text(
        "export function helper() { return 1; }\n"
    )
    (tmp_path / "main.ts").write_text(
        "import { helper } from './util';\n"
        "export function run() { helper(); }\n"
    )
    from graphlens_typescript._resolver import TsResolver

    r = TsResolver()
    r.prepare(tmp_path, list(tmp_path.glob("*.ts")))
    if r._disabled:
        pytest.skip("typescript install unavailable")

    # 'helper' callee in main.ts line 2, 1-based col 25
    # line: 'export function run() { helper(); }'
    #        0         1         2
    #        0123456789012345678901234567890
    # 'helper' starts at index 24 (0-based) → col 25 (1-based)
    refs = r.resolve_all([(tmp_path / "main.ts", 2, 25)])
    assert refs[0] is not None, (
        "TsResolver returned None — bridge may have failed to resolve"
    )
    assert refs[0].full_name == "helper"
    assert str(refs[0].file_path).endswith("util.ts")
    assert refs[0].origin == "internal"
