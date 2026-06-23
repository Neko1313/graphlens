r"""
Dependency file parsers for PHP / Composer projects.

PHP poses a classification wrinkle: Composer package names (``vendor/pkg``)
are not the namespaces that ``use`` statements reference. There is no
reliable manifest-only mapping from ``symfony/console`` to
``Symfony\\Component\\Console`` without inspecting the installed package.

These parsers therefore return the set of **vendor prefixes** (the part
before the ``/``, lowercased), which the :class:`ImportClassifier` matches
against the lowercased top-level segment of an imported namespace. This
resolves the common case (``Symfony`` ↔ ``symfony/*``, ``Monolog`` ↔
``monolog/monolog``, ``Psr`` ↔ ``psr/log``) from the manifest alone; the
type-aware resolver corrects the rest from the real ``vendor/`` tree when it
is installed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from graphlens.contracts import DependencyFileParser

if TYPE_CHECKING:
    from pathlib import Path


def _vendor_prefix(package: str) -> str:
    """Return the lowercased vendor segment of a ``vendor/package`` name."""
    if not isinstance(package, str) or "/" not in package:
        return ""
    return package.split("/", maxsplit=1)[0].strip().lower()


class ComposerJsonDepsParser(DependencyFileParser):
    """
    Reads declared dependencies from ``composer.json``.

    Collects ``require`` and ``require-dev`` so test-only packages (e.g.
    ``phpunit/phpunit``) are classified as ``third_party`` rather than
    ``unknown``. Platform requirements (``php``, ``ext-*``, ``lib-*``) are
    skipped. Returns vendor prefixes (see module docstring).
    """

    def can_parse(self, project_root: Path) -> bool:
        return (project_root / "composer.json").exists()

    def parse(self, project_root: Path) -> frozenset[str]:
        path = project_root / "composer.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return frozenset()
        if not isinstance(data, dict):
            return frozenset()

        vendors: set[str] = set()
        for section in ("require", "require-dev"):
            block = data.get(section)
            if not isinstance(block, dict):
                continue
            for package in block:
                if package in ("php",) or package.startswith(
                    ("ext-", "lib-", "php-")
                ):
                    continue
                vendor = _vendor_prefix(package)
                if vendor:
                    vendors.add(vendor)
        return frozenset(vendors)


class ComposerLockDepsParser(DependencyFileParser):
    """
    Reads resolved packages from ``composer.lock``.

    Covers both ``packages`` and ``packages-dev`` so transitive dependencies
    that are imported directly still classify as ``third_party``.
    """

    def can_parse(self, project_root: Path) -> bool:
        return (project_root / "composer.lock").exists()

    def parse(self, project_root: Path) -> frozenset[str]:
        path = project_root / "composer.lock"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return frozenset()
        if not isinstance(data, dict):
            return frozenset()

        vendors: set[str] = set()
        for section in ("packages", "packages-dev"):
            block = data.get(section)
            if not isinstance(block, list):
                continue
            for entry in block:
                if not isinstance(entry, dict):
                    continue
                vendor = _vendor_prefix(entry.get("name", ""))
                if vendor:
                    vendors.add(vendor)
        return frozenset(vendors)


# ---------------------------------------------------------------------------
# Default parser list for PhpAdapter
# ---------------------------------------------------------------------------

PHP_DEFAULT_DEP_PARSERS: list[DependencyFileParser] = [
    ComposerJsonDepsParser(),
    ComposerLockDepsParser(),
]


# ---------------------------------------------------------------------------
# Built-in / "stdlib" names
# ---------------------------------------------------------------------------
#
# PHP has no module-style standard library: built-in functions are global,
# and built-in classes live in the global namespace. A ``use`` of one of
# these names (always a single, unqualified segment, e.g. ``use DateTime;``)
# is therefore the signal we classify as ``stdlib``.

_BUILTIN_CLASSES: frozenset[str] = frozenset({
    # Core / SPL
    "stdClass", "Closure", "Generator", "WeakMap", "WeakReference",
    "ArrayObject", "ArrayIterator", "ArrayAccess", "Countable", "Iterator",
    "IteratorAggregate", "Traversable", "Stringable", "JsonSerializable",
    "Serializable", "UnitEnum", "BackedEnum", "SplStack", "SplQueue",
    "SplDoublyLinkedList", "SplFixedArray", "SplObjectStorage",
    "SplPriorityQueue", "SplHeap", "SplMinHeap", "SplMaxHeap",
    "SplFileObject", "SplFileInfo", "SplTempFileObject", "DirectoryIterator",
    "RecursiveIteratorIterator", "RecursiveDirectoryIterator",
    "FilesystemIterator",
    # Exceptions / errors
    "Throwable", "Exception", "Error", "TypeError", "ValueError",
    "ArgumentCountError", "ArithmeticError", "DivisionByZeroError",
    "RuntimeException", "LogicException", "InvalidArgumentException",
    "OutOfRangeException", "OutOfBoundsException", "LengthException",
    "DomainException", "RangeException", "UnexpectedValueException",
    "UnderflowException", "OverflowException", "BadFunctionCallException",
    "BadMethodCallException", "JsonException",
    # Date / time
    "DateTime", "DateTimeImmutable", "DateTimeInterface", "DateInterval",
    "DateTimeZone", "DatePeriod",
    # Reflection
    "ReflectionClass", "ReflectionObject", "ReflectionMethod",
    "ReflectionFunction", "ReflectionProperty", "ReflectionParameter",
    "ReflectionNamedType", "ReflectionEnum", "ReflectionAttribute",
    "ReflectionException", "Attribute",
    # Common extensions bundled with PHP
    "PDO", "PDOStatement", "PDOException", "mysqli", "SQLite3",
    "DOMDocument", "DOMElement", "DOMNode", "SimpleXMLElement",
    "XMLReader", "XMLWriter", "ZipArchive", "finfo", "IntlDateFormatter",
    "NumberFormatter", "Collator", "Locale", "CURLFile", "GMP", "BcMath",
})


def get_stdlib_names() -> frozenset[str]:
    """Return the set of PHP built-in class names treated as ``stdlib``."""
    return _BUILTIN_CLASSES
