"""TypescriptAdapter — orchestrates TypeScript project analysis."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from graphlens import (
    GraphLens,
    LanguageAdapter,
    Node,
    NodeKind,
    Relation,
    RelationKind,
)
from graphlens.utils import make_node_id

from graphlens_typescript._deps import (
    TYPESCRIPT_DEFAULT_DEP_PARSERS,
    get_stdlib_names,
)
from graphlens_typescript._module_resolver import (
    file_to_qualified_name,
    find_source_roots,
)
from graphlens_typescript._project_detector import (
    detect_project_name,
    find_typescript_roots,
    is_typescript_project,
)
from graphlens_typescript._visitor import (
    ImportClassifier,
    TypescriptASTVisitor,
    VisitorContext,
    parse_typescript,
)

if TYPE_CHECKING:
    from pathlib import Path

    from graphlens.contracts import DependencyFileParser

logger = logging.getLogger("graphlens_typescript")

_STDLIB = get_stdlib_names()

# Declaration files contain only type information — skip them during analysis
_DECLARATION_SUFFIXES: tuple[str, ...] = (".d.ts", ".d.mts", ".d.cts")


class TypescriptAdapter(LanguageAdapter):
    """Language adapter for TypeScript projects."""

    def __init__(
        self,
        dep_parsers: list[DependencyFileParser] | None = None,
    ) -> None:
        """
        Initialize the TypeScript adapter.

        Args:
            dep_parsers: parsers used to extract third-party dependency
                names from manifest files. Pass a custom list to support
                non-standard package managers.
                Defaults to ``TYPESCRIPT_DEFAULT_DEP_PARSERS``.

        """
        self._dep_parsers = (
            dep_parsers
            if dep_parsers is not None
            else TYPESCRIPT_DEFAULT_DEP_PARSERS
        )

    def language(self) -> str:
        return "typescript"

    def file_extensions(self) -> set[str]:
        return {".ts", ".tsx", ".mts", ".cts"}

    def can_handle(self, project_root: Path) -> bool:
        return is_typescript_project(project_root)

    def collect_files(self, project_root: Path) -> list[Path]:
        """
        Collect TypeScript source files, excluding declaration files.

        Declaration files (``.d.ts``, ``.d.mts``, ``.d.cts``) contain only
        type information and no implementation — they are skipped.
        """
        files = super().collect_files(project_root)
        return [
            f for f in files
            if not any(str(f).endswith(suf) for suf in _DECLARATION_SUFFIXES)
        ]

    def analyze(
        self,
        project_root: Path,
        files: list[Path] | None = None,
    ) -> GraphLens:
        graph = GraphLens()

        if files is not None:
            _analyze_root(
                graph,
                project_root,
                project_root,
                files,
                self._dep_parsers,
            )
        else:
            for lang_root in find_typescript_roots(project_root):
                root_files = self.collect_files(lang_root)
                _analyze_root(
                    graph,
                    project_root,
                    lang_root,
                    root_files,
                    self._dep_parsers,
                )

        return graph


def _analyze_root(
    graph: GraphLens,
    project_root: Path,
    lang_root: Path,
    files: list[Path],
    dep_parsers: list[DependencyFileParser],
) -> None:
    """Analyze one TypeScript project root and populate graph in-place."""
    project_name = detect_project_name(lang_root)
    source_roots = find_source_roots(lang_root, files)

    # Pre-pass: collect internal top-level names from file paths (no parsing)
    internal_tops: set[str] = set()
    for f in files:
        sr = _find_source_root_for(f, source_roots) or source_roots[0]
        try:
            qname = file_to_qualified_name(f, sr)
            internal_tops.add(qname.split(".")[0])
        except ValueError:
            pass

    # Parse dependency manifests
    third_party: set[str] = set()
    for parser in dep_parsers:
        if parser.can_parse(lang_root):
            third_party.update(parser.parse(lang_root))

    classifier = ImportClassifier(
        stdlib=_STDLIB,
        third_party=frozenset(third_party),
        internal=frozenset(internal_tops),
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

    for file in files:
        source_root = (
            _find_source_root_for(file, source_roots) or source_roots[0]
        )

        try:
            module_qname = file_to_qualified_name(file, source_root)
        except ValueError:
            logger.warning(
                "Cannot compute qualified name for %s, skipping", file
            )
            continue

        _ensure_module_chain(graph, project_name, module_qname, modules)

        try:
            relative_path = str(file.relative_to(project_root))
        except ValueError:
            relative_path = str(file.relative_to(lang_root))

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
            leaf_module_id = modules[module_qname]
            graph.add_relation(
                Relation(
                    source_id=leaf_module_id,
                    target_id=file_id,
                    kind=RelationKind.CONTAINS,
                )
            )

        try:
            source_bytes = file.read_bytes()
        except OSError as e:
            logger.warning("Cannot read %s: %s — skipping", file, e)
            continue

        is_tsx = file.suffix.lower() == ".tsx"
        tree = parse_typescript(source_bytes, tsx=is_tsx)
        if tree.root_node.has_error:
            logger.warning(
                "Parse errors in %s — continuing with partial results",
                file,
            )

        ctx = VisitorContext(
            project_name=project_name,
            file_path=file,
            source_root=source_root,
            module_qualified_name=module_qname,
        )
        visitor = TypescriptASTVisitor(
            ctx, graph, file_id, source_bytes, classifier
        )
        visitor.visit(tree.root_node)

    # PROJECT --CONTAINS--> top-level modules
    top_level = {qn: mid for qn, mid in modules.items() if "." not in qn}
    for module_id in top_level.values():
        graph.add_relation(
            Relation(
                source_id=project_id,
                target_id=module_id,
                kind=RelationKind.CONTAINS,
            )
        )


def _find_source_root_for(file: Path, source_roots: list[Path]) -> Path | None:
    for root in source_roots:
        try:
            file.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def _ensure_module_chain(
    graph: GraphLens,
    project_name: str,
    module_qname: str,
    modules: dict[str, str],
) -> str:
    """
    Ensure MODULE nodes exist for the full chain a.b.c.

    Returns the node ID of the leaf module.
    Creates CONTAINS relations between parent and child modules.
    """
    parts = module_qname.split(".")
    parent_id: str | None = None

    for i in range(1, len(parts) + 1):
        qname = ".".join(parts[:i])
        if qname not in modules:
            node_id = make_node_id(project_name, qname, NodeKind.MODULE.value)
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

    return modules[module_qname]
