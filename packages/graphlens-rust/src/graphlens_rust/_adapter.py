"""RustAdapter — orchestrates structural analysis of Rust crates."""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from graphlens import (
    RESOLVER_STATUS_KEY,
    AdapterError,
    GraphLens,
    LanguageAdapter,
    Node,
    NodeKind,
    Relation,
    RelationKind,
    ResolverStatus,
)
from graphlens.utils import make_node_id
from graphlens.utils.roots import filter_nested_root_files

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

logger = logging.getLogger("graphlens_rust")


class RustAdapter(LanguageAdapter):
    """Language adapter for Rust crates (structure + imports)."""

    def __init__(
        self,
        dep_parsers: list[DependencyFileParser] | None = None,
        resolver: SymbolResolver | None = None,
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
                graph, project_root, project_root, files, self._dep_parsers
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


def _analyze_root(
    graph: GraphLens,
    project_root: Path,
    crate_root: Path,
    files: list[Path],
    dep_parsers: list[DependencyFileParser],
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
        ctx = RustFileContext(
            project_name=crate_name,
            module_qname=module_qname,
            file_id=file_id,
            file_rel=graph.nodes[file_id].qualified_name,
        )
        RustStructureExtractor(graph, ctx, classify).extract(
            parse_rust(source).root_node
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
