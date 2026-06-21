"""RustAdapter — orchestrates structural analysis of Rust crates."""

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
from graphlens.utils import make_node_id
from graphlens.utils.roots import filter_nested_root_files

from graphlens_rust._boundary import (
    RUST_DEFAULT_BOUNDARY_EXTRACTORS,
    RustBoundaryExtractor,
)
from graphlens_rust._deps import (
    RUST_DEFAULT_DEP_PARSERS,
    classify_rust_import,
    read_crate_name,
)
from graphlens_rust._project_detector import find_rust_roots, is_rust_project
from graphlens_rust._resolver import RustResolver
from graphlens_rust._visitor import (
    RustFileContext,
    RustStructureExtractor,
    parse_rust,
)

if TYPE_CHECKING:
    from graphlens.contracts import DependencyFileParser, SymbolResolver
    from tree_sitter import Node as TSNode

logger = logging.getLogger("graphlens_rust")


class RustAdapter(LanguageAdapter):
    """Language adapter for Rust crates (structure + imports)."""

    def __init__(
        self,
        dep_parsers: list[DependencyFileParser] | None = None,
        resolver: SymbolResolver | None = None,
        boundary_extractors: list[RustBoundaryExtractor] | None = None,
    ) -> None:
        """Initialise with optional custom dep parsers and resolver."""
        self._dep_parsers = (
            dep_parsers
            if dep_parsers is not None
            else RUST_DEFAULT_DEP_PARSERS
        )
        self._resolver = (
            resolver if resolver is not None else RustResolver()
        )
        self._boundary_extractors = (
            boundary_extractors
            if boundary_extractors is not None
            else RUST_DEFAULT_BOUNDARY_EXTRACTORS
        )

    def language(self) -> str:
        return "rust"

    def file_extensions(self) -> set[str]:
        return {".rs"}

    def can_handle(self, project_root: str | Path) -> bool:
        return is_rust_project(Path(project_root))

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
                self._boundary_extractors,
            )
            self._resolver.prepare(project_root, files)
            statuses.append(self._resolver.status())
        else:
            roots = find_rust_roots(project_root)
            for crate_root in roots:
                root_files = filter_nested_root_files(
                    self.collect_files(crate_root), crate_root, roots
                )
                _analyze_root(
                    graph,
                    project_root,
                    crate_root,
                    root_files,
                    self._dep_parsers,
                    self._boundary_extractors,
                )
                self._resolver.prepare(crate_root, root_files)
                statuses.append(self._resolver.status())

        status = ResolverStatus.combine(statuses)
        graph.metadata[RESOLVER_STATUS_KEY] = status.value
        if strict and status is not ResolverStatus.OK:
            msg = (
                f"Rust resolver status is '{status.value}'; refusing to "
                "return a degraded graph in strict mode"
            )
            raise AdapterError(msg)
        return graph


def _module_qname(file: Path, crate_root: Path, crate_name: str) -> str:
    try:
        rel = file.relative_to(crate_root / "src")
    except ValueError:
        try:
            rel = file.relative_to(crate_root)
        except ValueError:
            return crate_name
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] in ("lib", "main", "mod"):
        parts = parts[:-1]
    return "::".join([crate_name, *parts]) if parts else crate_name


def _analyze_root(  # noqa: PLR0913
    graph: GraphLens,
    project_root: Path,
    crate_root: Path,
    files: list[Path],
    dep_parsers: list[DependencyFileParser],
    boundary_extractors: list[RustBoundaryExtractor],
) -> None:
    """Analyse one Rust crate root and populate ``graph`` in place."""
    crate_name = read_crate_name(crate_root) or crate_root.name

    required: set[str] = set()
    for parser in dep_parsers:
        if parser.can_parse(crate_root):
            required |= parser.parse(crate_root)
    classify = partial(
        classify_rust_import, crate_name=crate_name, deps=required
    )

    project_id = make_node_id(
        crate_name, crate_name, NodeKind.PROJECT.value
    )
    if project_id not in graph.nodes:
        graph.add_node(
            Node(
                id=project_id,
                kind=NodeKind.PROJECT,
                qualified_name=crate_name,
                name=crate_name,
            )
        )

    modules: dict[str, str] = {}
    parsed_files: list[tuple[str, str, TSNode]] = []
    for file in files:
        module_qname = _module_qname(file, crate_root, crate_name)
        module_id = _ensure_module(
            graph, crate_name, module_qname, project_id, modules
        )
        file_id = _ensure_file(
            graph, crate_name, project_root, crate_root, file, module_id
        )
        try:
            source = file.read_bytes()
        except OSError as exc:
            logger.warning("Cannot read %s: %s — skipping", file, exc)
            continue
        file_rel = graph.nodes[file_id].qualified_name
        ctx = RustFileContext(
            project_name=crate_name,
            module_qname=module_qname,
            file_id=file_id,
            file_rel=file_rel,
        )
        root = parse_rust(source).root_node
        RustStructureExtractor(graph, ctx, classify).extract(root)
        parsed_files.append((file_rel, file_id, root))

    _extract_boundaries(graph, parsed_files, boundary_extractors)


def _extract_boundaries(
    graph: GraphLens,
    parsed_files: list[tuple[str, str, TSNode]],
    extractors: list[RustBoundaryExtractor],
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


def _ensure_module(
    graph: GraphLens,
    crate_name: str,
    module_qname: str,
    project_id: str,
    modules: dict[str, str],
) -> str:
    if module_qname in modules:
        return modules[module_qname]
    module_id = make_node_id(
        crate_name, module_qname, NodeKind.MODULE.value
    )
    graph.add_node(
        Node(
            id=module_id,
            kind=NodeKind.MODULE,
            qualified_name=module_qname,
            name=module_qname.rsplit("::", maxsplit=1)[-1],
        )
    )
    graph.add_relation(
        Relation(project_id, module_id, RelationKind.CONTAINS)
    )
    modules[module_qname] = module_id
    return module_id


def _ensure_file(  # noqa: PLR0913
    graph: GraphLens,
    crate_name: str,
    project_root: Path,
    crate_root: Path,
    file: Path,
    module_id: str,
) -> str:
    try:
        file_rel = str(file.relative_to(project_root))
    except ValueError:
        file_rel = str(file.relative_to(crate_root))
    file_id = make_node_id(crate_name, file_rel, NodeKind.FILE.value)
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
