"""Module qualified name resolution and source root detection."""

from __future__ import annotations

import json
import re
from pathlib import Path

# Matches "prefix/*": ["./target/*"] — single target, glob on both sides
_ALIAS_RE = re.compile(
    r'"([^"]+)/\*"\s*:\s*\[\s*"\./([^"]*)/\*"\s*\]'
)

# Extensions to strip when converting file path to module name
_TS_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
    }
)

# Files that represent the package root (like __init__.py in Python)
_INDEX_STEMS: frozenset[str] = frozenset({"index"})


def find_source_roots(project_root: Path, files: list[Path]) -> list[Path]:
    """
    Detect TypeScript source roots.

    Prefers a ``src/`` sub-directory when source files live there,
    but also includes ``project_root`` for files outside ``src/``.
    Falls back to ``[project_root]`` for non-src-layout projects.
    """
    src = project_root / "src"
    if src.is_dir() and files and any(f.is_relative_to(src) for f in files):
        return [src, project_root]
    return [project_root]


def load_tsconfig_path_aliases(project_root: Path) -> dict[str, str]:
    """
    Read ``tsconfig.json`` and return a prefix-alias map.

    Extracts ``compilerOptions.paths`` entries of the form
    ``"<prefix>/*": ["<target>/*"]`` and converts them to
    ``{"<prefix>/": "<target>/"}`` (stripping the ``/*`` glob and
    leading ``./``).  Multi-target entries and non-glob patterns are
    silently ignored.  Returns ``{}`` on any error — never raises.
    """
    tsconfig = project_root / "tsconfig.json"
    try:
        raw = tsconfig.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        # Strip // line comments and trailing commas before parsing
        clean = re.sub(r"//[^\n]*", "", raw)
        clean = re.sub(r",(\s*[}\]])", r"\1", clean)
        data = json.loads(clean)
        paths = data.get("compilerOptions", {}).get("paths", {})
        if not isinstance(paths, dict):
            return {}
    except Exception:
        return {}
    aliases: dict[str, str] = {}
    for alias_pattern, targets in paths.items():
        if not alias_pattern.endswith("/*"):
            continue
        if not isinstance(targets, list) or len(targets) != 1:
            continue
        target = targets[0]
        if not isinstance(target, str) or not target.endswith("/*"):
            continue
        alias_prefix = alias_pattern[:-1]  # strip trailing *
        target_prefix = target.lstrip("./")[:-1]  # strip ./ and trailing *
        aliases[alias_prefix] = target_prefix
    return aliases


def apply_path_alias(import_path: str, aliases: dict[str, str]) -> str:
    """
    Rewrite ``import_path`` if it matches a tsconfig path alias prefix.

    For example, with ``aliases = {"@/": "src/"}``, rewrites
    ``"@/client/v2"`` → ``"src/client/v2"``.
    Returns ``import_path`` unchanged when no alias prefix matches.
    """
    for prefix, target in aliases.items():
        if import_path.startswith(prefix):
            return target + import_path[len(prefix):]
    return import_path


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
