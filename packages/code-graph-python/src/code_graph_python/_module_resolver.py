"""Module qualified name resolution and source root detection."""

from __future__ import annotations

from pathlib import Path


def find_source_roots(project_root: Path, files: list[Path]) -> list[Path]:
    """
    Detect Python source roots.

    Checks for src/ layout first. Falls back to project root.
    """
    src = project_root / "src"
    if (
        src.is_dir()
        and any(files)
        and any(f.is_relative_to(src) for f in files)
    ):
        return [src]
    return [project_root]


def file_to_qualified_name(file_path: Path, source_root: Path) -> str:
    """
    Convert a file path to a dotted Python qualified module name.

    Examples:
      src/mypackage/__init__.py  ->  "mypackage"
      src/mypackage/utils.py     ->  "mypackage.utils"
      src/mypackage/sub/__init__.py -> "mypackage.sub"

    """
    relative = file_path.relative_to(source_root)
    parts = list(relative.parts)

    # Strip .py / .pyi extension from last part
    stem = Path(parts[-1]).stem
    parts[-1] = stem

    # For __init__, the module is the package itself (drop __init__)
    if parts[-1] == "__init__":
        parts = parts[:-1]

    if not parts:
        # Top-level __init__.py with no parent — use the source root name
        return source_root.name

    return ".".join(parts)


def is_package_init(file_path: Path) -> bool:
    """Return True if the file is __init__.py or __init__.pyi."""
    return file_path.name in ("__init__.py", "__init__.pyi")


def resolve_relative_import(
    current_module_qname: str,
    level: int,
    module: str | None,
) -> str:
    """
    Resolve a relative import to an absolute qualified name.

    Args:
        current_module_qname: e.g. ``'mypackage.sub.mod'`` or
            ``'mypackage'`` (for __init__)
        level: number of leading dots (1 = current package,
            2 = parent package, etc.)
        module: the module part, e.g. ``'utils'`` in
            ``'from ..utils import x'``. Can be None.

    The current module's *package* is all parts except the last.
    For level=1: base = current package (drop last part of
    current_module_qname if it's not a package __init__).
    For level=2: go one more level up, etc.

    """
    parts = current_module_qname.split(".")
    # The package is everything except the module name itself.
    # We go up `level` levels from the current module's package.
    # parts[:-1] = current package. Then go up (level - 1) more.
    base_parts = parts[: max(0, len(parts) - level)]

    if module:
        return ".".join([*base_parts, module]) if base_parts else module
    return ".".join(base_parts) if base_parts else ""
