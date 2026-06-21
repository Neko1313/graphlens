"""GoAdapter — orchestrates structural analysis of Go projects."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from graphlens import (
    RESOLVER_STATUS_KEY,
    AdapterError,
    BoundaryRef,
    GraphLens,
    LanguageAdapter,
    Node,
    NodeKind,
    Relation,
    RelationKind,
    ResolverStatus,
    make_boundary_id,
)
from graphlens.utils import SpanIndex, make_node_id
from graphlens.utils.roots import filter_nested_root_files

from graphlens_go._boundary import (
    GO_DEFAULT_BOUNDARY_EXTRACTORS,
    GoBoundaryExtractor,
)
from graphlens_go._deps import (
    GO_DEFAULT_DEP_PARSERS,
    classify_go_import,
    read_module_path,
)
from graphlens_go._project_detector import find_go_roots, is_go_project
from graphlens_go._resolver import GoResolver
from graphlens_go._visitor import (
    GoFileContext,
    GoStructureExtractor,
    parse_go,
)

if TYPE_CHECKING:
    from graphlens.contracts import DependencyFileParser, SymbolResolver
    from tree_sitter import Node as TSNode

    from graphlens_go._visitor import OccurrenceRef

# Occurrence role -> the edge kind the resolution pass emits for it.
_ROLE_TO_KIND = {"call": RelationKind.CALLS}

logger = logging.getLogger("graphlens_go")


class GoAdapter(LanguageAdapter):
    """Language adapter for Go projects (structure + imports)."""

    def __init__(
        self,
        dep_parsers: list[DependencyFileParser] | None = None,
        resolver: SymbolResolver | None = None,
        boundary_extractors: list[GoBoundaryExtractor] | None = None,
    ) -> None:
        """Initialise with optional custom dep parsers and resolver."""
        self._dep_parsers = (
            dep_parsers
            if dep_parsers is not None
            else GO_DEFAULT_DEP_PARSERS
        )
        self._resolver = resolver if resolver is not None else GoResolver()
        self._boundary_extractors = (
            boundary_extractors
            if boundary_extractors is not None
            else GO_DEFAULT_BOUNDARY_EXTRACTORS
        )

    def language(self) -> str:
        return "go"

    def file_extensions(self) -> set[str]:
        return {".go"}

    def can_handle(self, project_root: str | Path) -> bool:
        return is_go_project(Path(project_root))

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

        if files is not None:
            _analyze_root(
                graph,
                project_root,
                project_root,
                files,
                self._dep_parsers,
                self._resolver,
                self._boundary_extractors,
            )
            statuses.append(self._resolver.status())
        else:
            for go_root in find_go_roots(project_root):
                root_files = filter_nested_root_files(
                    self.collect_files(go_root),
                    go_root,
                    find_go_roots(project_root),
                )
                _analyze_root(
                    graph,
                    project_root,
                    go_root,
                    root_files,
                    self._dep_parsers,
                    self._resolver,
                    self._boundary_extractors,
                )
                statuses.append(self._resolver.status())

        status = ResolverStatus.combine(statuses)
        graph.metadata[RESOLVER_STATUS_KEY] = status.value
        if strict and status is not ResolverStatus.OK:
            msg = (
                f"Go resolver status is '{status.value}'; refusing to "
                "return a degraded graph in strict mode"
            )
            raise AdapterError(msg)
        return graph


def _analyze_root(  # noqa: PLR0913
    graph: GraphLens,
    project_root: Path,
    go_root: Path,
    files: list[Path],
    dep_parsers: list[DependencyFileParser],
    resolver: SymbolResolver,
    boundary_extractors: list[GoBoundaryExtractor],
) -> None:
    """Analyse one Go module root and populate ``graph`` in place."""
    module_path = read_module_path(go_root) or go_root.name
    project_name = module_path.rstrip("/").split("/")[-1]

    required: set[str] = set()
    for parser in dep_parsers:
        if parser.can_parse(go_root):
            required |= parser.parse(go_root)
    classify = partial(
        classify_go_import, module_path=module_path, required=required
    )

    project_id = make_node_id(
        project_name, module_path, NodeKind.PROJECT.value
    )
    if project_id not in graph.nodes:
        graph.add_node(
            Node(
                id=project_id,
                kind=NodeKind.PROJECT,
                qualified_name=module_path,
                name=project_name,
            )
        )

    packages: dict[str, str] = {}
    parsed_files: list[tuple[str, str, TSNode]] = []
    occurrences: list[tuple[str, OccurrenceRef]] = []
    for file in files:
        pkg_qname = _package_qname(file, go_root, module_path)
        module_id = _ensure_package(
            graph, project_name, pkg_qname, project_id, packages
        )
        file_id = _ensure_file(
            graph, project_name, project_root, go_root, file, module_id
        )
        try:
            source = file.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s — skipping", file, exc)
            continue
        file_rel = graph.nodes[file_id].qualified_name
        ctx = GoFileContext(
            project_name=project_name,
            package_qname=pkg_qname,
            file_id=file_id,
            file_rel=file_rel,
        )
        root = parse_go(source).root_node
        extractor = GoStructureExtractor(graph, ctx, classify)
        extractor.extract(root)
        parsed_files.append((file_rel, file_id, root))
        occurrences.extend(
            (str(file), occ) for occ in extractor.occurrences
        )

    # Resolution pass: bind occurrences to real nodes or EXTERNAL_SYMBOL.
    span_index = SpanIndex.from_graph(graph)
    resolver.prepare(go_root, files)
    _resolve_occurrences(
        graph, project_name, resolver, span_index, occurrences
    )

    _extract_boundaries(graph, parsed_files, boundary_extractors)


def _ensure_external_symbol(
    graph: GraphLens, project_name: str, qname: str, origin: str
) -> str:
    """Return the id of an EXTERNAL_SYMBOL node, creating it if needed."""
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
) -> None:
    """Resolve each occurrence to a definition node and emit its edge."""
    for abs_path, occ in occurrences:
        rel_kind = _ROLE_TO_KIND[occ.role]
        ref = resolver.definition_at(Path(abs_path), occ.line, occ.col)
        if ref is None:
            continue
        target_id: str | None = None
        if ref.origin == "internal" and ref.file_path is not None:
            target_id = span_index.at(
                str(ref.file_path), ref.line, ref.col
            )
        if target_id is None:
            fallback_qname = (
                ref.full_name
                if ref.full_name
                else f"{occ.role}@{occ.line}:{occ.col}"
            )
            target_id = _ensure_external_symbol(
                graph, project_name, fallback_qname, ref.origin
            )
        graph.add_relation(
            Relation(
                source_id=occ.enclosing_id,
                target_id=target_id,
                kind=rel_kind,
                metadata={"span": occ.span},
            )
        )


def _extract_boundaries(
    graph: GraphLens,
    parsed_files: list[tuple[str, str, TSNode]],
    extractors: list[GoBoundaryExtractor],
) -> None:
    """Run boundary extractors and emit BOUNDARY nodes + EXPOSES/CONSUMES."""
    if not extractors:
        return
    enclosers: dict[str, list[Node]] = {}
    for node in graph.nodes.values():
        if (
            node.kind in (NodeKind.FUNCTION, NodeKind.METHOD)
            and node.span is not None
            and node.file_path is not None
        ):
            enclosers.setdefault(node.file_path, []).append(node)

    for file_rel, file_id, root in parsed_files:
        candidates = enclosers.get(file_rel, [])
        for extractor in extractors:
            for ref in extractor.extract(root):
                enclosing_id = (
                    _innermost_enclosing(candidates, ref.line, ref.col)
                    or file_id
                )
                _add_boundary(graph, enclosing_id, ref)


def _innermost_enclosing(
    candidates: list[Node], line: int, col: int
) -> str | None:
    """Return the id of the deepest function/method containing (line, col)."""
    best_id: str | None = None
    best_start: tuple[int, int] | None = None
    for node in candidates:
        span = node.span
        if span is None:
            continue  # pragma: no cover - filtered before insertion
        start = (span.start_line, span.start_col)
        end = (span.end_line, span.end_col)
        if start <= (line, col) <= end and (
            best_start is None or start >= best_start
        ):
            best_id = node.id
            best_start = start
    return best_id


def _add_boundary(
    graph: GraphLens, enclosing_id: str, ref: BoundaryRef
) -> None:
    boundary_id = make_boundary_id(ref.mechanism, ref.key)
    if boundary_id not in graph.nodes:
        graph.add_node(
            Node(
                id=boundary_id,
                kind=NodeKind.BOUNDARY,
                qualified_name=f"{ref.mechanism}:{ref.key}",
                name=ref.key,
                metadata={"mechanism": ref.mechanism, "key": ref.key},
            )
        )
    kind = (
        RelationKind.EXPOSES
        if ref.role == "server"
        else RelationKind.CONSUMES
    )
    metadata: dict[str, object] = {
        "mechanism": ref.mechanism,
        "key": ref.key,
        "confidence": ref.confidence,
        "role": ref.role,
        "line": ref.line,
        "col": ref.col,
    }
    metadata.update(ref.detail)
    graph.add_relation(
        Relation(
            source_id=enclosing_id,
            target_id=boundary_id,
            kind=kind,
            metadata=metadata,
        )
    )


def _package_qname(file: Path, go_root: Path, module_path: str) -> str:
    rel_dir = file.parent.relative_to(go_root)
    if str(rel_dir) == ".":
        return module_path
    return f"{module_path}/{rel_dir.as_posix()}"


def _ensure_package(
    graph: GraphLens,
    project_name: str,
    pkg_qname: str,
    project_id: str,
    packages: dict[str, str],
) -> str:
    if pkg_qname in packages:
        return packages[pkg_qname]
    module_id = make_node_id(project_name, pkg_qname, NodeKind.MODULE.value)
    graph.add_node(
        Node(
            id=module_id,
            kind=NodeKind.MODULE,
            qualified_name=pkg_qname,
            name=pkg_qname.rsplit("/", maxsplit=1)[-1],
        )
    )
    graph.add_relation(
        Relation(project_id, module_id, RelationKind.CONTAINS)
    )
    packages[pkg_qname] = module_id
    return module_id


def _ensure_file(  # noqa: PLR0913
    graph: GraphLens,
    project_name: str,
    project_root: Path,
    go_root: Path,
    file: Path,
    module_id: str,
) -> str:
    try:
        file_rel = str(file.relative_to(project_root))
    except ValueError:
        file_rel = str(file.relative_to(go_root))
    file_id = make_node_id(project_name, file_rel, NodeKind.FILE.value)
    if file_id not in graph.nodes:
        graph.add_node(
            Node(
                id=file_id,
                kind=NodeKind.FILE,
                qualified_name=file_rel,
                name=file.name,
                file_path=file_rel,
            )
        )
        graph.add_relation(
            Relation(module_id, file_id, RelationKind.CONTAINS)
        )
    return file_id
