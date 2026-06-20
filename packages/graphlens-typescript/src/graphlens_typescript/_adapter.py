"""TypescriptAdapter — orchestrates TypeScript project analysis."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from graphlens import (
    GraphLens,
    LanguageAdapter,
    Node,
    NodeKind,
    Relation,
    RelationKind,
)
from graphlens.utils import SpanIndex, make_node_id
from graphlens.utils.roots import filter_nested_root_files

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
from graphlens_typescript._resolver import TsResolver
from graphlens_typescript._visitor import (
    ImportClassifier,
    OccurrenceRef,
    TypescriptASTVisitor,
    VisitorContext,
    parse_typescript,
)

if TYPE_CHECKING:
    from graphlens.contracts import DependencyFileParser, SymbolResolver

logger = logging.getLogger("graphlens_typescript")

_STDLIB = get_stdlib_names()

# Declaration files contain only type information — skip them during analysis
_DECLARATION_SUFFIXES: tuple[str, ...] = (".d.ts", ".d.mts", ".d.cts")

# ---------------------------------------------------------------------------
# Role → RelationKind mapping
# ---------------------------------------------------------------------------

_ROLE_TO_KIND: dict[str, RelationKind] = {
    "call": RelationKind.CALLS,
    "base": RelationKind.INHERITS_FROM,
    "annotation": RelationKind.HAS_TYPE,
    "read": RelationKind.REFERENCES,
    "write": RelationKind.REFERENCES,
}


class TypescriptAdapter(LanguageAdapter):
    """Language adapter for TypeScript projects."""

    def __init__(
        self,
        dep_parsers: list[DependencyFileParser] | None = None,
        resolver: SymbolResolver | None = None,
    ) -> None:
        """
        Initialize the TypeScript adapter.

        Args:
            dep_parsers: parsers used to extract third-party dependency
                names from manifest files. Pass a custom list to support
                non-standard package managers.
                Defaults to ``TYPESCRIPT_DEFAULT_DEP_PARSERS``.
            resolver: symbol resolver used for cross-file resolution of
                calls, references, annotations, and base classes.
                Defaults to ``TsResolver``. Inject a custom or null
                resolver to override resolution behaviour.

        """
        self._dep_parsers = (
            dep_parsers
            if dep_parsers is not None
            else TYPESCRIPT_DEFAULT_DEP_PARSERS
        )
        self._resolver: SymbolResolver = (
            resolver if resolver is not None else TsResolver()
        )

    def language(self) -> str:
        """Return the language identifier for this adapter."""
        return "typescript"

    def file_extensions(self) -> set[str]:
        """Return the set of file extensions handled by this adapter."""
        return {".ts", ".tsx", ".mts", ".cts"}

    def can_handle(self, project_root: Path) -> bool:
        """Return True if the project root is a TypeScript project."""
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
        """
        Analyze a TypeScript project and return a populated GraphLens.

        Args:
            project_root: the root directory of the project (or monorepo).
            files: optional explicit list of files to analyze; when omitted
                all TypeScript source files are collected automatically.

        Returns:
            A ``GraphLens`` containing the structural and relational nodes.

        """
        graph = GraphLens()

        if files is not None:
            _analyze_root(
                graph,
                project_root,
                project_root,
                files,
                self._dep_parsers,
                self._resolver,
            )
        else:
            lang_roots = find_typescript_roots(project_root)
            for lang_root in lang_roots:
                root_files = self.collect_files(lang_root)
                root_files = filter_nested_root_files(
                    root_files,
                    lang_root,
                    lang_roots,
                )
                _analyze_root(
                    graph,
                    project_root,
                    lang_root,
                    root_files,
                    self._dep_parsers,
                    self._resolver,
                )

        return graph


def _ensure_external_symbol(
    graph: GraphLens, project_name: str, qname: str, origin: str
) -> str:
    """
    Return the id of an EXTERNAL_SYMBOL node for ``qname``.

    Creates the node if it does not yet exist in ``graph``.

    Args:
        graph: the graph to update in-place.
        project_name: used as the namespace for ``make_node_id``.
        qname: fully-qualified name of the external symbol.
        origin: one of ``"stdlib"``, ``"third_party"``, ``"unknown"``,
            or ``"internal"`` (fallback when the module node is absent).

    Returns:
        The node id of the EXTERNAL_SYMBOL.

    """
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
    """
    Resolve all accumulated occurrences and emit edges (batched).

    Collects all (file, line, col) queries in one list, issues a single
    ``resolver.resolve_all(queries)`` call, then maps results back to
    graph edges.

    For each ``(abs_path, occ)`` pair:

    1. Receive the definition site from the batch result.
    2. If ``ref is None`` — skip.
    3. If the definition is internal, look up the target node id via
       ``span_index.at()``.
    4. If the node is not found (or origin is external), create/reuse an
       ``EXTERNAL_SYMBOL`` fallback node.
    5. Emit a ``Relation`` of the appropriate kind, with span metadata
       and, for read/write occurrences, an ``access`` key.

    Args:
        graph: the graph to update in-place.
        project_name: namespace used for EXTERNAL_SYMBOL node ids.
        resolver: the symbol resolver that was already ``prepare()``d.
        span_index: pre-built index of node spans from ``graph``.
        occurrences: list of ``(absolute_file_path, OccurrenceRef)`` pairs
            collected during the file-visit loop.

    """
    queries: list[tuple[Path, int, int]] = [
        (Path(p), o.line, o.col) for (p, o) in occurrences
    ]
    refs = resolver.resolve_all(queries)
    for (_p, occ), ref in zip(occurrences, refs, strict=True):
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
                graph,
                project_name,
                fallback_qname,
                ref.origin,
            )
        metadata: dict[str, object] = {"span": occ.span}
        if occ.role in ("read", "write"):
            metadata["access"] = occ.role
        graph.add_relation(
            Relation(
                source_id=occ.enclosing_id,
                target_id=target_id,
                kind=_ROLE_TO_KIND[occ.role],
                metadata=metadata,
            )
        )


def _analyze_root(  # noqa: PLR0913, PLR0915
    graph: GraphLens,
    project_root: Path,
    lang_root: Path,
    files: list[Path],
    dep_parsers: list[DependencyFileParser],
    resolver: SymbolResolver,
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
    all_occurrences: list[tuple[str, OccurrenceRef]] = []

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
            file_relative_path=relative_path,
            source_root=source_root,
            module_qualified_name=module_qname,
            modules=modules,
        )
        visitor = TypescriptASTVisitor(
            ctx, graph, file_id, source_bytes, classifier
        )
        visitor.visit(tree.root_node)
        all_occurrences.extend(
            (visitor.abs_file_path, o) for o in visitor.occurrences
        )

    # Resolution pass: bind occurrences to real nodes or EXTERNAL_SYMBOL
    span_index = SpanIndex.from_graph(graph)
    resolver.prepare(lang_root, files)
    _resolve_occurrences(
        graph, project_name, resolver, span_index, all_occurrences
    )

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
