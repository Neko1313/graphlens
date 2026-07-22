"""
Namespace resolution and csproj metadata reading for C#.

C# namespaces are declared in source (``namespace X.Y;`` or ``namespace X.Y
{ ... }``) and, unlike PHP's PSR-4, are *not* bound to file paths by any
manifest. What the project's ``.csproj`` does provide is the assembly's
``<RootNamespace>`` / ``<AssemblyName>`` — the default namespace top used to
tell an in-project namespace (``internal``) apart from a NuGet or BCL one.
These helpers therefore read csproj metadata (manifest-only, no source
parsing); the authoritative namespace of a type still comes from its
in-source declaration during the CST walk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from xml.etree import ElementTree

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def local_name(tag: str) -> str:
    """Return an XML tag's local name, dropping any ``{namespace}`` prefix."""
    return tag.rpartition("}")[2]


def parse_xml(path: Path) -> ElementTree.Element | None:
    """Parse an XML file, returning its root element or ``None`` on error."""
    try:
        # Local project files, not untrusted network input; Python 3.13's
        # expat has built-in entity-amplification limits.
        return ElementTree.parse(path).getroot()  # noqa: S314
    except (OSError, ElementTree.ParseError):
        return None


def iter_by_local(
    root: ElementTree.Element, name: str
) -> Iterator[ElementTree.Element]:
    """
    Yield every descendant element whose local tag name equals ``name``.

    Iterating by local name makes the walk agnostic to the MSBuild XML
    namespace that legacy (non-SDK) ``.csproj`` files declare.
    """
    for element in root.iter():
        if local_name(element.tag) == name:
            yield element


def first_csproj(project_root: Path) -> Path | None:
    """Return the first ``*.csproj`` directly under ``project_root``."""
    return next(iter(sorted(project_root.glob("*.csproj"))), None)


def csproj_properties(project_root: Path) -> dict[str, str]:
    """
    Read ``RootNamespace``/``AssemblyName``/``PackageId`` from the csproj.

    Returns a dict with only the keys actually present (never raises).
    """
    props: dict[str, str] = {}
    csproj = first_csproj(project_root)
    if csproj is None:
        return props
    root = parse_xml(csproj)
    if root is None:
        return props
    for tag in ("RootNamespace", "AssemblyName", "PackageId"):
        for element in iter_by_local(root, tag):
            text = (element.text or "").strip()
            if text:
                props[tag] = text
                break
    return props


def _namespace_from_csproj(csproj: Path) -> str:
    root = parse_xml(csproj)
    if root is not None:
        for tag in ("RootNamespace", "AssemblyName"):
            for element in iter_by_local(root, tag):
                text = (element.text or "").strip()
                if text:
                    return text
    return csproj.stem


def internal_namespace_tops(project_root: Path) -> set[str]:
    """
    Return the top segment of every csproj default namespace under root.

    Walks every ``.csproj`` in the tree (a solution may hold many) so a
    ``using`` whose first segment matches any first-party assembly's root
    namespace classifies as ``internal``.
    """
    # Deferred: _project_detector imports from this module, so importing it
    # back at module level would create a circular import.
    import graphlens_csharp._project_detector as _pd  # noqa: PLC0415

    tops: set[str] = set()
    for csproj in sorted(project_root.rglob("*.csproj")):
        if _pd.EXCLUDED_DIRS & set(csproj.relative_to(project_root).parts):
            continue
        namespace = _namespace_from_csproj(csproj)
        if namespace:  # pragma: no cover - always non-empty (csproj stem)
            tops.add(namespace.split(".", maxsplit=1)[0])
    return tops
