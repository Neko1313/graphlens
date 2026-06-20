"""GoAdapter — orchestrates structural analysis of Go projects."""

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

logger = logging.getLogger("graphlens_go")


class GoAdapter(LanguageAdapter):
    """Language adapter for Go projects (structure + imports)."""

    def __init__(
        self,
        dep_parsers: list[DependencyFileParser] | None = None,
        resolver: SymbolResolver | None = None,
    ) -> None:
        """Initialise with optional custom dep parsers and resolver."""
        self._dep_parsers = (
            dep_parsers
            if dep_parsers is not None
            else GO_DEFAULT_DEP_PARSERS
        )
        self._resolver = resolver if resolver is not None else GoResolver()

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
                graph, project_root, project_root, files, self._dep_parsers
            )
            self._resolver.prepare(project_root, files)
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
                )
                self._resolver.prepare(go_root, root_files)
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


def _analyze_root(
    graph: GraphLens,
    project_root: Path,
    go_root: Path,
    files: list[Path],
    dep_parsers: list[DependencyFileParser],
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
        ctx = GoFileContext(
            project_name=project_name,
            package_qname=pkg_qname,
            file_id=file_id,
            file_rel=graph.nodes[file_id].qualified_name,
        )
        GoStructureExtractor(graph, ctx, classify).extract(
            parse_go(source).root_node
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
