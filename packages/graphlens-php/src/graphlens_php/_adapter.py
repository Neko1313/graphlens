"""PhpAdapter — orchestrates PHP project analysis."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from graphlens import (
    RESOLVER_METRICS_KEY,
    RESOLVER_STATUS_KEY,
    AdapterError,
    GraphLens,
    LanguageAdapter,
    Node,
    NodeKind,
    Relation,
    RelationKind,
    ResolverMetrics,
    ResolverStatus,
)
from graphlens.utils import SpanIndex, make_node_id
from graphlens.utils.roots import filter_nested_root_files

from graphlens_php._deps import (
    PHP_DEFAULT_DEP_PARSERS,
    get_stdlib_names,
)
from graphlens_php._module_resolver import (
    internal_namespace_tops,
    path_to_namespace,
)
from graphlens_php._project_detector import (
    EXCLUDED_DIRS,
    detect_project_name,
    find_php_roots,
    is_php_project,
)
from graphlens_php._resolver import PhpantomResolver
from graphlens_php._visitor import (
    ImportClassifier,
    OccurrenceRef,
    PhpASTVisitor,
    VisitorContext,
    extract_namespace,
    parse_php,
)

if TYPE_CHECKING:
    from graphlens.contracts import DependencyFileParser, SymbolResolver

logger = logging.getLogger("graphlens_php")

_STDLIB = get_stdlib_names()

# Role → RelationKind mapping for the resolution pass.
_ROLE_TO_KIND: dict[str, RelationKind] = {
    "call": RelationKind.CALLS,
    "base": RelationKind.INHERITS_FROM,
    "annotation": RelationKind.HAS_TYPE,
    "read": RelationKind.REFERENCES,
    "write": RelationKind.REFERENCES,
}


class PhpAdapter(LanguageAdapter):
    """Language adapter for PHP / Composer projects."""

    def __init__(
        self,
        dep_parsers: list[DependencyFileParser] | None = None,
        resolver: SymbolResolver | None = None,
    ) -> None:
        """
        Initialize the PHP adapter.

        Args:
            dep_parsers: parsers used to extract Composer vendor prefixes from
                manifest files (composer.json, composer.lock). Pass a custom
                list to support non-standard setups. Defaults to
                ``PHP_DEFAULT_DEP_PARSERS``.
            resolver: symbol resolver used for cross-file resolution of calls,
                references, type uses, and base classes. Defaults to
                ``PhpantomResolver`` (requires the ``phpantom_lsp`` Rust binary
                in PATH). Pass ``PhpactorResolver()`` to use phpactor instead,
                a ``PhpResolver`` instance to build a structure-only graph, or
                inject a custom ``SymbolResolver`` subclass.

        """
        self._dep_parsers = (
            dep_parsers
            if dep_parsers is not None
            else PHP_DEFAULT_DEP_PARSERS
        )
        self._resolver = (
            resolver if resolver is not None else PhpantomResolver()
        )

    def language(self) -> str:
        return "php"

    def file_extensions(self) -> set[str]:
        return {".php", ".phtml", ".inc"}

    def can_handle(self, project_root: str | Path) -> bool:
        return is_php_project(Path(project_root))

    def collect_files(self, project_root: str | Path) -> list[Path]:
        """
        Return all PHP source files under ``project_root``.

        Overrides the core default to also skip PHP-specific non-source
        directories — most importantly ``vendor/`` (Composer's installed
        third-party tree, PHP's equivalent of ``node_modules``), plus build
        and cache dirs. Without this a real app with dependencies installed
        would index thousands of third-party files as project source.
        """
        root = Path(project_root)
        extensions = self.file_extensions()
        return sorted(
            p
            for p in root.rglob("*")
            if p.is_file()
            and p.suffix in extensions
            and not (EXCLUDED_DIRS & set(p.relative_to(root).parts))
        )

    def analyze(
        self,
        project_root: str | Path,
        files: list[Path] | None = None,
        *,
        strict: bool = False,
    ) -> GraphLens:
        project_root = Path(project_root).resolve()
        graph = GraphLens()
        statuses: list[ResolverStatus] = []
        metrics = ResolverMetrics()

        if files is not None:
            metrics.merge(
                _analyze_root(
                    graph,
                    project_root,
                    project_root,
                    files,
                    self._dep_parsers,
                    self._resolver,
                )
            )
            statuses.append(self._resolver.status())
        else:
            php_roots = find_php_roots(project_root)
            for php_root in php_roots:
                root_files = self.collect_files(php_root)
                root_files = filter_nested_root_files(
                    root_files,
                    php_root,
                    php_roots,
                )
                metrics.merge(
                    _analyze_root(
                        graph,
                        project_root,
                        php_root,
                        root_files,
                        self._dep_parsers,
                        self._resolver,
                    )
                )
                statuses.append(self._resolver.status())

        status = ResolverStatus.combine(statuses)
        graph.metadata[RESOLVER_STATUS_KEY] = status.value
        graph.metadata[RESOLVER_METRICS_KEY] = metrics.as_dict()
        if strict and status is not ResolverStatus.OK:
            msg = (
                f"PHP resolver status is '{status.value}'; refusing to "
                "return a degraded graph in strict mode"
            )
            raise AdapterError(msg)
        return graph


def _analyze_root(  # noqa: PLR0913
    graph: GraphLens,
    project_root: Path,
    php_root: Path,
    files: list[Path],
    dep_parsers: list[DependencyFileParser],
    resolver: SymbolResolver,
) -> ResolverMetrics:
    """Analyze one PHP project root and populate graph in-place."""
    project_name = detect_project_name(php_root)

    classifier = ImportClassifier(
        stdlib=_STDLIB,
        third_party=_collect_third_party(php_root, dep_parsers),
        internal=frozenset(internal_namespace_tops(php_root)),
    )

    project_id = make_node_id(
        project_name, project_name, NodeKind.PROJECT.value
    )
    if project_id not in graph.nodes:
        graph.add_node(
            Node(
                id=project_id,
                kind=NodeKind.PROJECT,
                qualified_name=project_name,
                name=project_name,
            )
        )

    modules: dict[str, str] = {}
    all_occurrences: list[tuple[str, OccurrenceRef]] = []

    for file in files:
        try:
            source_bytes = file.read_bytes()
        except OSError as e:
            logger.warning("Cannot read %s: %s — skipping", file, e)
            continue

        tree = parse_php(source_bytes)
        if tree.root_node.has_error:
            logger.warning(
                "Parse errors in %s — continuing with partial results", file
            )

        namespace = extract_namespace(tree.root_node) or path_to_namespace(
            file, php_root
        )

        try:
            relative_path = str(file.relative_to(project_root))
        except ValueError:  # pragma: no cover - unusual monorepo layout
            relative_path = str(file.relative_to(php_root))

        file_id = make_node_id(
            project_name, relative_path, NodeKind.FILE.value
        )
        if file_id not in graph.nodes:
            graph.add_node(
                Node(
                    id=file_id,
                    kind=NodeKind.FILE,
                    qualified_name=relative_path,
                    name=file.name,
                    file_path=relative_path,
                )
            )
            container_id = (
                _ensure_module_chain(
                    graph, project_name, namespace, modules
                )
                if namespace
                else project_id
            )
            graph.add_relation(
                Relation(
                    source_id=container_id,
                    target_id=file_id,
                    kind=RelationKind.CONTAINS,
                )
            )

        ctx = VisitorContext(
            project_name=project_name,
            file_path=file,
            namespace=namespace,
        )
        visitor = PhpASTVisitor(
            ctx, graph, file_id, source_bytes, classifier, modules
        )
        visitor.visit(tree.root_node)
        all_occurrences.extend(
            (visitor.abs_file_path, o) for o in visitor.occurrences
        )

    # Resolution pass: bind occurrences to real nodes or EXTERNAL_SYMBOL.
    span_index = SpanIndex.from_graph(graph)
    resolver.prepare(php_root, files)
    metrics = _resolve_occurrences(
        graph, project_name, resolver, span_index, all_occurrences
    )

    # PROJECT --CONTAINS--> top-level namespace modules.
    for qname, module_id in modules.items():
        if "\\" not in qname:
            graph.add_relation(
                Relation(
                    source_id=project_id,
                    target_id=module_id,
                    kind=RelationKind.CONTAINS,
                )
            )
    return metrics


def _collect_third_party(
    php_root: Path, dep_parsers: list[DependencyFileParser]
) -> frozenset[str]:
    third_party: set[str] = set()
    for parser in dep_parsers:
        if parser.can_parse(php_root):
            third_party.update(parser.parse(php_root))
    return frozenset(third_party)


def _ensure_external_symbol(
    graph: GraphLens, project_name: str, qname: str, origin: str
) -> str:
    """Return the id of an EXTERNAL_SYMBOL node for ``qname`` (creates it)."""
    sym_id = make_node_id(
        project_name, qname, NodeKind.EXTERNAL_SYMBOL.value
    )
    if sym_id not in graph.nodes:
        graph.add_node(
            Node(
                id=sym_id,
                kind=NodeKind.EXTERNAL_SYMBOL,
                qualified_name=qname,
                name=qname.rsplit("\\", maxsplit=1)[-1],
                metadata={"origin": origin},
            )
        )
    return sym_id


def _resolve_occurrences(
    graph: GraphLens,
    project_name: str,
    resolver: SymbolResolver,
    span_index: SpanIndex,
    occurrences: list[tuple[str, OccurrenceRef]],
) -> ResolverMetrics:
    """Resolve accumulated occurrences and emit edges (batched)."""
    metrics = ResolverMetrics(queries=len(occurrences))
    if not occurrences:
        return metrics
    queries: list[tuple[Path, int, int]] = [
        (Path(p), o.line, o.col) for (p, o) in occurrences
    ]
    start = time.perf_counter()
    refs = resolver.resolve_all(queries)
    metrics.seconds = time.perf_counter() - start
    for (_p, occ), ref in zip(occurrences, refs, strict=True):
        if ref is None:
            metrics.unresolved += 1
            continue
        metrics.resolved += 1
        rel_kind = _ROLE_TO_KIND[occ.role]
        target_id: str | None = None
        if ref.origin == "internal" and ref.file_path is not None:
            target_id = span_index.at(
                str(ref.file_path), ref.line, ref.col
            )
        if target_id is None:
            metrics.external += 1
            fallback_qname = (
                ref.full_name
                if ref.full_name
                else f"{occ.role}@{occ.line}:{occ.col}"
            )
            target_id = _ensure_external_symbol(
                graph, project_name, fallback_qname, ref.origin
            )
        else:
            metrics.internal += 1
        metadata: dict[str, object] = {"span": occ.span}
        if occ.role in ("read", "write"):
            metadata["access"] = occ.role
        graph.add_relation(
            Relation(
                source_id=occ.enclosing_id,
                target_id=target_id,
                kind=rel_kind,
                metadata=metadata,
            )
        )
    return metrics


def _ensure_module_chain(
    graph: GraphLens,
    project_name: str,
    namespace: str,
    modules: dict[str, str],
) -> str:
    r"""
    Ensure MODULE nodes exist for the full namespace chain ``A\\B\\C``.

    Returns the node ID of the leaf namespace module and links parents to
    children via CONTAINS.
    """
    parts = namespace.split("\\")
    parent_id: str | None = None

    for i in range(1, len(parts) + 1):
        qname = "\\".join(parts[:i])
        if qname not in modules:
            node_id = make_node_id(
                project_name, qname, NodeKind.MODULE.value
            )
            graph.add_node(
                Node(
                    id=node_id,
                    kind=NodeKind.MODULE,
                    qualified_name=qname,
                    name=parts[i - 1],
                )
            )
            modules[qname] = node_id

            if parent_id is not None:
                graph.add_relation(
                    Relation(
                        source_id=parent_id,
                        target_id=node_id,
                        kind=RelationKind.CONTAINS,
                    )
                )

        parent_id = modules[qname]

    return modules[namespace]
