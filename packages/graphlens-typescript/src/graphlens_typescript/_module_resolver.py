"""Module qualified name resolution and source root detection."""

from __future__ import annotations

from pathlib import Path

# Extensions to strip when converting file path to module name
_TS_EXTENSIONS: frozenset[str] = frozenset({
    ".ts", ".tsx", ".mts", ".cts",
})

# Files that represent the package root (like __init__.py in Python)
_INDEX_STEMS: frozenset[str] = frozenset({"index"})


def find_source_roots(project_root: Path, files: list[Path]) -> list[Path]:
    """
    Detect TypeScript source roots.

    Prefers a ``src/`` sub-directory when source files live there.
    Falls back to ``project_root``.
    """
    src = project_root / "src"
    if (
        src.is_dir()
        and files
        and any(f.is_relative_to(src) for f in files)
    ):
        return [src]
    return [project_root]


def file_to_qualified_name(file_path: Path, source_root: Path) -> str:
    """
    Convert a TypeScript file path to a dotted module qualified name.

    Examples:
      src/mypackage/index.ts   ->  ``"mypackage"``
      src/mypackage/utils.ts   ->  ``"mypackage.utils"``
      src/mypackage/ui.tsx     ->  ``"mypackage.ui"``

    Declaration files (.d.ts) follow the same mapping — they are filtered
    out at the adapter level, but the resolver handles them correctly.

    """
    relative = file_path.relative_to(source_root)
    parts = list(relative.parts)

    # Strip TypeScript extension from last segment
    last = Path(parts[-1])
    # Handle compound extensions like .d.ts, .d.mts
    if last.suffix in _TS_EXTENSIONS:
        stem = last.stem
        # Strip inner .d suffix for declaration files (e.g. foo.d → foo)
        if stem.endswith(".d"):
            stem = stem[:-2]
        parts[-1] = stem
    else:
        parts[-1] = last.stem

    # Drop index files (they represent the package itself, like __init__.py)
    if parts and parts[-1] in _INDEX_STEMS:
        parts = parts[:-1]

    if not parts:
        return source_root.name

    return ".".join(parts)


def resolve_relative_import(
    current_module_qname: str,
    import_path: str,
) -> str:
    """
    Resolve a TypeScript relative import path to an absolute qualified name.

    Args:
        current_module_qname: dotted name of the module that contains the
            import statement, e.g. ``"mypackage.core"``.
        import_path: raw import path string (already stripped of quotes),
            e.g. ``"./utils"``, ``"../shared"``, ``"."``.

    Examples:
        resolve_relative_import("mypackage.core", "./utils")
            -> "mypackage.utils"
        resolve_relative_import("mypackage.core", "../shared")
            -> "shared"
        resolve_relative_import("mypackage.core", ".")
            -> "mypackage"

    """
    current_parts = current_module_qname.split(".")
    # Start at the directory containing the current file (drop module name)
    base_parts: list[str] = (
        current_parts[:-1] if len(current_parts) > 1 else []
    )

    for segment in import_path.replace("\\", "/").split("/"):
        if segment in ("", "."):
            pass  # stay at current level
        elif segment == "..":
            base_parts = base_parts[:-1] if base_parts else []
        else:
            # Strip file extensions if present in the import path
            stem = segment.split(".")[0] if "." in segment else segment
            if stem and stem not in _INDEX_STEMS:
                base_parts = [*base_parts, stem]
            # For "index" imports stay at the current package level

    if not base_parts:
        # Went above root — return the top-level part of the original qname
        return current_parts[0]
    return ".".join(base_parts)
