# `_adapter.py` Template

Replace all placeholders before using:
- `{lang}` → snake_case language name (e.g. `typescript`)
- `{Lang}` → PascalCase (e.g. `Typescript`)
- `{LANG}` → UPPER_CASE (e.g. `TYPESCRIPT`)
- `{language}` → human-readable (e.g. `TypeScript`)
- `{ext}` → primary file extension (e.g. `.ts`)

```python
"""{Lang}Adapter — orchestrates {language} project analysis."""

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

from graphlens_{lang}._deps import (
    {LANG}_DEFAULT_DEP_PARSERS,
    get_stdlib_names,
)
from graphlens_{lang}._module_resolver import (
    file_to_qualified_name,
    find_source_roots,
)
from graphlens_{lang}._project_detector import (
    detect_project_name,
    find_{lang}_roots,
    is_{lang}_project,
)
from graphlens_{lang}._visitor import (
    ImportClassifier,
    {Lang}ASTVisitor,
    VisitorContext,
    parse_{lang},
)

if TYPE_CHECKING:
    from pathlib import Path

    from graphlens.contracts import DependencyFileParser

logger = logging.getLogger("graphlens_{lang}")

_STDLIB = get_stdlib_names()


class {Lang}Adapter(LanguageAdapter):
    """Language adapter for {language} projects."""

    def __init__(
        self,
        dep_parsers: list[DependencyFileParser] | None = None,
    ) -> None:
        """
        Initialize the {language} adapter.

        Args:
            dep_parsers: parsers used to extract third-party dependency
                names from manifest files. Pass a custom list to support
                non-standard package managers.
                Defaults to ``{LANG}_DEFAULT_DEP_PARSERS``.

        """
        self._dep_parsers = (
            dep_parsers
            if dep_parsers is not None
            else {LANG}_DEFAULT_DEP_PARSERS
        )

    def language(self) -> str:
        return "{lang}"

    def file_extensions(self) -> set[str]:
        return {"{ext}"}  # extend with all handled extensions

    def can_handle(self, project_root: Path) -> bool:
        return is_{lang}_project(project_root)

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
            for lang_root in find_{lang}_roots(project_root):
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
    """Analyze one {language} project root and populate graph in-place."""
    project_name = detect_project_name(lang_root)
    source_roots = find_source_roots(lang_root, files)

    # --- Pre-pass: collect all internal module top-level names ---------------
    # Derive module qnames from file paths without parsing source — so the
    # ImportClassifier knows which imports are internal before visiting.
    internal_tops: set[str] = set()
    for f in files:
        sr = _find_source_root_for(f, source_roots) or source_roots[0]
        try:
            qname = file_to_qualified_name(f, sr)
            internal_tops.add(qname.split(".")[0])
        except ValueError:
            pass

    # --- Third-party: parse dependency manifests ----------------------------
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

        # FILE node — path relative to original project_root so all paths in
        # a monorepo share the same reference point.
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

        tree = parse_{lang}(source_bytes)
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
        visitor = {Lang}ASTVisitor(
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
```
