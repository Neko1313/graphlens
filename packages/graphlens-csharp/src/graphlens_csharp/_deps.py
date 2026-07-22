r"""
Dependency file parsers for C# / NuGet projects.

Like PHP's Composer wrinkle, a NuGet package id is not always the namespace a
``using`` references — but for the overwhelming majority of packages the two
share a top segment (``Newtonsoft.Json`` the package ↔ ``Newtonsoft.Json`` the
namespace; ``Serilog`` ↔ ``Serilog``). These parsers therefore return the set
of lowercased **top segments** of declared package ids, which the
:class:`ImportClassifier` matches against the lowercased first segment of an
imported namespace. This resolves the common case from the manifest alone; the
type-aware resolver corrects the rest from the real assembly graph when a C#
language server is available.

Three manifest shapes are covered: SDK-style ``<PackageReference>`` in
``.csproj``, legacy ``packages.config``, and Central Package Management's
``Directory.Packages.props`` (``<PackageVersion>``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphlens.contracts import DependencyFileParser

from graphlens_csharp._module_resolver import iter_by_local, parse_xml

if TYPE_CHECKING:
    from pathlib import Path


def _top_segment(package_id: str) -> str:
    """Return the lowercased top segment of a dotted NuGet package id."""
    if not isinstance(package_id, str):
        return ""
    return package_id.split(".", maxsplit=1)[0].strip().lower()


class CsprojDepsParser(DependencyFileParser):
    """
    Reads ``<PackageReference>`` items from every ``.csproj`` in a root.

    Both the SDK-style ``Include=`` form and Central Package Management's
    ``Update=`` form are collected. Returns package-id top segments (see
    module docstring).
    """

    def can_parse(self, project_root: Path) -> bool:
        return any(project_root.glob("*.csproj"))

    def parse(self, project_root: Path) -> frozenset[str]:
        tops: set[str] = set()
        for csproj in sorted(project_root.glob("*.csproj")):
            root = parse_xml(csproj)
            if root is None:
                continue
            for ref in iter_by_local(root, "PackageReference"):
                package = ref.get("Include") or ref.get("Update") or ""
                top = _top_segment(package)
                if top:
                    tops.add(top)
        return frozenset(tops)


class PackagesConfigDepsParser(DependencyFileParser):
    """Reads ``<package id="..." />`` entries from ``packages.config``."""

    def can_parse(self, project_root: Path) -> bool:
        return (project_root / "packages.config").exists()

    def parse(self, project_root: Path) -> frozenset[str]:
        root = parse_xml(project_root / "packages.config")
        if root is None:
            return frozenset()
        tops: set[str] = set()
        for package in iter_by_local(root, "package"):
            top = _top_segment(package.get("id") or "")
            if top:
                tops.add(top)
        return frozenset(tops)


class DirectoryPackagesPropsDepsParser(DependencyFileParser):
    """
    Reads ``<PackageVersion>`` items from ``Directory.Packages.props``.

    This is NuGet Central Package Management: versions are declared centrally
    while each csproj keeps a version-less ``<PackageReference>``. Parsing it
    catches packages a project uses even when the csproj alone omits versions.
    """

    def can_parse(self, project_root: Path) -> bool:
        return (project_root / "Directory.Packages.props").exists()

    def parse(self, project_root: Path) -> frozenset[str]:
        root = parse_xml(project_root / "Directory.Packages.props")
        if root is None:
            return frozenset()
        tops: set[str] = set()
        for version in iter_by_local(root, "PackageVersion"):
            top = _top_segment(version.get("Include") or "")
            if top:
                tops.add(top)
        return frozenset(tops)


# ---------------------------------------------------------------------------
# Default parser list for CsharpAdapter
# ---------------------------------------------------------------------------

CSHARP_DEFAULT_DEP_PARSERS: list[DependencyFileParser] = [
    CsprojDepsParser(),
    PackagesConfigDepsParser(),
    DirectoryPackagesPropsDepsParser(),
]


# ---------------------------------------------------------------------------
# Built-in / "stdlib" names
# ---------------------------------------------------------------------------
#
# C#'s standard library is the Base Class Library, rooted at the ``System``
# namespace. Everything under ``System.*`` is runtime-provided, so the top
# segment ``System`` is the signal we classify as ``stdlib``. ``Microsoft.*``
# is deliberately *not* treated as stdlib: most ``Microsoft.*`` namespaces
# (``Microsoft.Extensions.*``, ``Microsoft.EntityFrameworkCore``, …) ship as
# independent NuGet packages, so classifying them here would mislabel
# third-party code. The type-aware resolver refines origin when present.

_BCL_NAMESPACE_TOPS: frozenset[str] = frozenset({"System"})


def get_stdlib_names() -> frozenset[str]:
    """Return the namespace top segments treated as C# ``stdlib`` (BCL)."""
    return _BCL_NAMESPACE_TOPS
