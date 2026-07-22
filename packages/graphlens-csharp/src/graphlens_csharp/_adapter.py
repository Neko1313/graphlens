"""CsharpAdapter — orchestrates C# project analysis."""

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

from graphlens_csharp._deps import (
    CSHARP_DEFAULT_DEP_PARSERS,
    get_stdlib_names,
)
from graphlens_csharp._module_resolver import internal_namespace_tops
from graphlens_csharp._project_detector import (
    EXCLUDED_DIRS,
    detect_project_name,
    find_csharp_roots,
    is_csharp_project,
)
from graphlens_csharp._resolver import CsharpScipResolver
from graphlens_csharp._visitor import (
    CsharpASTVisitor,
    ImportClassifier,
    OccurrenceRef,
    VisitorContext,
    extract_namespace,
    parse_csharp,
)

if TYPE_CHECKING:
    from graphlens.contracts import DependencyFileParser, SymbolResolver

logger = logging.getLogger("graphlens_csharp")

_STDLIB = get_stdlib_names()

# Role → RelationKind mapping for the resolution pass.
_ROLE_TO_KIND: dict[str, RelationKind] = {
    "call": RelationKind.CALLS,
    "base": RelationKind.INHERITS_FROM,
    "annotation": RelationKind.HAS_TYPE,
    "read": RelationKind.REFERENCES,
    "write": RelationKind.REFERENCES,
}


class CsharpAdapter(LanguageAdapter):
    """Language adapter for C# / .NET projects."""

    def __init__(
        self,
        dep_parsers: list[DependencyFileParser] | None = None,
        resolver: SymbolResolver | None = None,
    ) -> None:
        """
        Initialize the C# adapter.

        Args:
            dep_parsers: parsers used to extract NuGet package ids from
                manifest files (``.csproj``, ``packages.config``,
                ``Directory.Packages.props``). Pass a custom list for
                non-standard setups. Defaults to the built-in NuGet parsers.
            resolver: symbol resolver used for cross-file resolution of calls,
                references, type uses, and base types. Defaults to
                ``CsharpScipResolver`` (a batch SCIP index via ``scip-dotnet``;
                degrades to a structure-only graph when it is absent). Pass a
                ``CsharpLspResolver`` (drives the ``csharp-ls`` Roslyn LSP
                server) for live queries against a running workspace instead,
                or inject a custom ``SymbolResolver`` subclass to override.

        """
        self._dep_parsers = (
            dep_parsers
            if dep_parsers is not None
            else CSHARP_DEFAULT_DEP_PARSERS
        )
        self._resolver = (
            resolver if resolver is not None else CsharpScipResolver()
        )

    def language(self) -> str:
        return "csharp"

    def file_extensions(self) -> set[str]:
        return {".cs"}

    def can_handle(self, project_root: str | Path) -> bool:
        return is_csharp_project(Path(project_root))

    def collect_files(self, project_root: str | Path) -> list[Path]:
        """
        Return all C# source files under ``project_root``.

        Overrides the core default to also skip C#-specific non-source
        directories — most importantly ``bin/`` and ``obj/`` (build output,
        which contains generated ``.cs`` such as ``*.AssemblyInfo.cs``), plus
        the legacy ``packages/`` NuGet tree.
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

        if files is not None:
            root_files = [(project_root, files)]
        else:
            csharp_roots = find_csharp_roots(project_root)
            root_files = [
                (
                    csharp_root,
                    filter_nested_root_files(
                        self.collect_files(csharp_root),
                        csharp_root,
                        csharp_roots,
                    ),
                )
                for csharp_root in csharp_roots
            ]

        # Phase 1 — structure for every project root, no resolution yet, so
        # the SpanIndex below spans the whole workspace and cross-root
        # definition targets already exist before any occurrence resolves.
        built = [
            _build_root_structure(
                graph, project_root, csharp_root, root_file_list,
                self._dep_parsers,
            )
            for csharp_root, root_file_list in root_files
        ]

        # Phase 2 — a SINGLE csharp-ls rooted at project_root resolves every
        # root. Rooting one server at project_root (instead of one per root)
        # is what lets cross-project references resolve (a solution's project
        # references) and avoids reloading the whole workspace once per root.
        all_files = [f for _r, fs in root_files for f in fs]
        self._resolver.prepare(project_root, all_files)
        span_index = SpanIndex.from_graph(graph)
        metrics = ResolverMetrics()
        for _project_id, project_name, occurrences, _modules in built:
            metrics.merge(
                _resolve_occurrences(
                    graph, project_name, self._resolver, span_index,
                    occurrences,
                )
            )

        # Phase 3 — PROJECT --CONTAINS--> top-level namespace modules.
        # Two sub-roots can share a project_id (identical AssemblyName) and
        # a top-level namespace, so dedup on (project_id, module_id) —
        # add_relation itself does not.
        linked: set[tuple[str, str]] = set()
        for project_id, _project_name, _occurrences, modules in built:
            for qname, module_id in modules.items():
                if "." not in qname and (project_id, module_id) not in linked:
                    linked.add((project_id, module_id))
                    graph.add_relation(
                        Relation(
                            source_id=project_id,
                            target_id=module_id,
                            kind=RelationKind.CONTAINS,
                        )
                    )

        status = self._resolver.status()
        graph.metadata[RESOLVER_STATUS_KEY] = status.value
        graph.metadata[RESOLVER_METRICS_KEY] = metrics.as_dict()
        if strict and status is not ResolverStatus.OK:
            msg = (
                f"C# resolver status is '{status.value}'; refusing to "
                "return a degraded graph in strict mode"
            )
            raise AdapterError(msg)
        return graph


def _build_root_structure(
    graph: GraphLens,
    project_root: Path,
    csharp_root: Path,
    files: list[Path],
    dep_parsers: list[DependencyFileParser],
) -> tuple[
    str,
    str,
    list[tuple[str, OccurrenceRef]],
    dict[str, str],
]:
    """
    Build structural nodes for one C# project root.

    Returns ``(project_id, project_name, occurrences, modules)``. Type-aware
    resolution runs later at the workspace level so a single project-rooted
    resolver and a full-graph ``SpanIndex`` serve every root (cross-root
    definitions only exist once every root's structure is built).
    """
    project_name = detect_project_name(csharp_root)

    classifier = ImportClassifier(
        stdlib=_STDLIB,
        third_party=_collect_third_party(csharp_root, dep_parsers),
        internal=frozenset(internal_namespace_tops(csharp_root)),
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

        tree = parse_csharp(source_bytes)
        if tree.root_node.has_error:
            logger.warning(
                "Parse errors in %s — continuing with partial results", file
            )

        # C# attributes a type to the namespace declared in source. A file
        # with no ``namespace`` is in the global namespace (unlike PHP's
        # PSR-4, there is no path-based fallback), so its FILE hangs directly
        # off the PROJECT node.
        namespace = extract_namespace(tree.root_node)

        try:
            relative_path = str(file.relative_to(project_root))
        except ValueError:  # pragma: no cover - unusual monorepo layout
            relative_path = str(file.relative_to(csharp_root))

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
        visitor = CsharpASTVisitor(
            ctx, graph, file_id, source_bytes, classifier, modules
        )
        visitor.visit(tree.root_node)
        all_occurrences.extend(
            (visitor.abs_file_path, o) for o in visitor.occurrences
        )

    return project_id, project_name, all_occurrences, modules


def _collect_third_party(
    csharp_root: Path, dep_parsers: list[DependencyFileParser]
) -> frozenset[str]:
    third_party: set[str] = set()
    for parser in dep_parsers:
        if parser.can_parse(csharp_root):
            third_party.update(parser.parse(csharp_root))
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
                name=qname.rsplit(".", maxsplit=1)[-1],
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
    """
    Ensure MODULE nodes exist for the full namespace chain ``A.B.C``.

    Returns the node ID of the leaf namespace module and links parents to
    children via CONTAINS.
    """
    parts = namespace.split(".")
    parent_id: str | None = None

    for i in range(1, len(parts) + 1):
        qname = ".".join(parts[:i])
        if qname not in modules:
            node_id = make_node_id(
                project_name, qname, NodeKind.MODULE.value
            )
            modules[qname] = node_id
            # Deterministic IDs mean a sibling root that shares this project
            # name and namespace already created this MODULE — register it
            # locally but don't re-add the node or its CONTAINS edge.
            if node_id not in graph.nodes:
                graph.add_node(
                    Node(
                        id=node_id,
                        kind=NodeKind.MODULE,
                        qualified_name=qname,
                        name=parts[i - 1],
                    )
                )
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
