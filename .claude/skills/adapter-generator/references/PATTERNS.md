# Code Patterns

## `_project_detector.py`

```python
"""{language} project detection: marker files and project name extraction."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

{LANG}_MARKERS: tuple[str, ...] = (
    "{marker}",        # primary marker, e.g. "package.json"
    # add more as needed
)

_EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".venv", "venv", "__pycache__", ".git",
    "dist", "build", ".eggs", "node_modules",
})


def is_{lang}_project(project_root: Path) -> bool:
    """
    Return True if the directory looks like a {language} project.

    Checks marker files first; falls back to presence of any source file.
    """
    if _has_{lang}_markers(project_root):
        return True
    return any(project_root.rglob("*{ext}"))


def find_{lang}_roots(search_root: Path) -> list[Path]:
    """
    Find {language} project roots within search_root (monorepo support).

    Returns [search_root] if search_root itself has markers.
    Otherwise walks subdirectories for marker files and returns distinct roots.
    Falls back to [search_root] if nothing found.
    """
    if _has_{lang}_markers(search_root):
        return [search_root]

    roots: list[Path] = []
    for marker in {LANG}_MARKERS:
        for marker_file in sorted(search_root.rglob(marker)):
            rel_parts = marker_file.relative_to(search_root).parts
            if _EXCLUDED_DIRS & set(rel_parts):
                continue
            candidate = marker_file.parent
            if any(
                candidate == r or candidate.is_relative_to(r)
                for r in roots
            ):
                continue
            roots.append(candidate)

    return sorted(roots) if roots else [search_root]


def detect_project_name(project_root: Path) -> str:
    """
    Extract the project name from manifest or fall back to directory name.

    Resolution order:
    1. {marker} "name" field (or equivalent)
    2. project_root directory name
    """
    manifest = project_root / "{marker}"
    if manifest.exists():
        try:
            # parse the manifest and extract name
            # e.g. for JSON: import json; data = json.loads(manifest.read_text()); return data.get("name", "")
            pass
        except Exception:
            pass
    return project_root.name


def _has_{lang}_markers(directory: Path) -> bool:
    return any((directory / m).exists() for m in {LANG}_MARKERS)
```

---

## `_module_resolver.py`

```python
"""Module qualified name resolution and source root detection."""

from __future__ import annotations

from pathlib import Path


def find_source_roots(project_root: Path, files: list[Path]) -> list[Path]:
    """
    Detect {language} source roots.

    Adapt to the language's conventions:
    - Python: src/ layout or project root
    - TypeScript: src/ layout, or project root
    - Go: module root (go.mod location)
    """
    src = project_root / "src"
    if src.is_dir() and any(f.is_relative_to(src) for f in files):
        return [src]
    return [project_root]


def file_to_qualified_name(file_path: Path, source_root: Path) -> str:
    """
    Convert a file path to a dotted module qualified name.

    Strip the source root prefix and file extension. Handle index files
    (the language equivalent of Python's __init__.py).

    Examples (TypeScript):
      src/mypackage/index.ts   ->  "mypackage"
      src/mypackage/utils.ts   ->  "mypackage.utils"
    """
    relative = file_path.relative_to(source_root)
    parts = list(relative.parts)

    # Strip extension from last segment
    stem = Path(parts[-1]).stem
    parts[-1] = stem

    # Drop index files (language equivalent of __init__)
    if parts[-1] in ("index",):   # adapt to target language
        parts = parts[:-1]

    if not parts:
        return source_root.name

    return ".".join(parts)


def resolve_relative_import(
    current_module_qname: str,
    level: int,
    module: str | None,
) -> str:
    """
    Resolve a relative import to an absolute qualified name.

    Adapt to the language's relative import syntax.
    For Python: level=1 → current package, level=2 → parent, etc.
    """
    parts = current_module_qname.split(".")
    base_parts = parts[: max(0, len(parts) - level)]
    if module:
        return ".".join([*base_parts, module]) if base_parts else module
    return ".".join(base_parts) if base_parts else ""
```

---

## `_deps.py`

```python
"""Dependency file parsers for {language} projects."""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphlens.contracts import DependencyFileParser, normalize_pkg_name

if TYPE_CHECKING:
    from pathlib import Path


class {Lang}ManifestParser(DependencyFileParser):
    """
    Reads declared dependencies from `{marker}`.

    Includes dev/test dependencies so test imports classify as third_party.
    """

    def can_parse(self, project_root: Path) -> bool:
        return (project_root / "{marker}").exists()

    def parse(self, project_root: Path) -> frozenset[str]:
        path = project_root / "{marker}"
        try:
            # Read and parse the manifest
            # Return frozenset of normalized package names
            names: set[str] = set()
            # ... parsing logic ...
            return frozenset(names)
        except Exception:
            return frozenset()   # ALWAYS return frozenset() on error


# ---------------------------------------------------------------------------
# Default parser list
# ---------------------------------------------------------------------------

{LANG}_DEFAULT_DEP_PARSERS: list[DependencyFileParser] = [
    {Lang}ManifestParser(),
    # add more parsers as needed (lockfile, workspace manifest, etc.)
]


# ---------------------------------------------------------------------------
# Stdlib / built-in names
# ---------------------------------------------------------------------------

def get_stdlib_names() -> frozenset[str]:
    """Return stdlib top-level module names for {language}."""
    return frozenset({
        # Add language built-in module names here
        # e.g. for Go: "fmt", "os", "io", "net", "http", ...
        # e.g. for TypeScript/Node: "fs", "path", "os", "crypto", ...
    })
```

### DependencyFileParser implementation examples by ecosystem

**package.json (Node.js/TypeScript)**:
```python
import json

def parse(self, project_root: Path) -> frozenset[str]:
    path = project_root / "package.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return frozenset()
    names: set[str] = set()
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        for dep in data.get(section, {}):
            n = normalize_pkg_name(dep)
            if n:
                names.add(n)
    return frozenset(names)
```

**Cargo.toml (Rust)**:
```python
import tomllib

def parse(self, project_root: Path) -> frozenset[str]:
    path = project_root / "Cargo.toml"
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except Exception:
        return frozenset()
    names: set[str] = set()
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        for dep in data.get(section, {}):
            n = normalize_pkg_name(dep)
            if n:
                names.add(n)
    return frozenset(names)
```

---

## `_visitor.py`

Parser setup (module-level singleton — one per adapter):

```python
import tree_sitter_{lang} as ts_{lang}
from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

_LANGUAGE = Language(ts_{lang}.language())
_parser = Parser(_LANGUAGE)


def parse_{lang}(source: bytes) -> object:
    """Parse {language} source bytes and return a tree-sitter Tree."""
    return _parser.parse(source)
```

### OccurrenceRef

```python
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class OccurrenceRef:
    """A single use-site collected during AST traversal."""
    role: str          # "call" | "read" | "write" | "annotation" | "base"
    file_path: Path
    line: int          # 1-based
    col: int           # 1-based
    enclosing_id: str  # node ID of the containing FUNCTION/METHOD/CLASS/FILE
```

### Visitor skeleton

```python
class {Lang}ASTVisitor:
    """
    Walks a tree-sitter {language} CST and populates a GraphLens.

    Emits structural nodes (CLASS, FUNCTION, METHOD, PARAMETER, IMPORT,
    VARIABLE, ATTRIBUTE, TYPE_ALIAS) and their containment/declaration edges.
    Records metadata["name_span"] on every structural node.
    Collects OccurrenceRef use-sites but does NOT emit CALLS, REFERENCES,
    HAS_TYPE, or INHERITS_FROM — those come from the post-visit resolution pass.
    """

    def __init__(
        self,
        ctx: VisitorContext,
        graph: GraphLens,
        file_node_id: str,
        source: bytes,
        classifier: ImportClassifier | None = None,
    ) -> None:
        self._ctx = ctx
        self._graph = graph
        self._file_node_id = file_node_id
        self._source = source
        self._classifier = classifier or ImportClassifier()
        self._scope_stack: list[str] = [ctx.module_qualified_name]
        self._container_stack: list[str] = [file_node_id]
        self._kind_stack: list[NodeKind] = [NodeKind.FILE]
        self.occurrences: list[OccurrenceRef] = []  # filled during traversal

    # -------------------------------------------------------------------------
    # Dispatch
    # -------------------------------------------------------------------------

    def visit(self, node: TSNode) -> None:
        handler = getattr(self, f"_visit_{node.type}", None)
        if handler:
            handler(node)
        else:
            self._visit_children(node)

    def _visit_children(self, node: TSNode) -> None:
        for child in node.children:
            self.visit(child)

    # -------------------------------------------------------------------------
    # Top-level node — visit children only
    # -------------------------------------------------------------------------

    def _visit_source_file(self, node: TSNode) -> None:  # adapt node type name
        self._visit_children(node)

    # -------------------------------------------------------------------------
    # Class / struct / interface
    # -------------------------------------------------------------------------

    def _visit_class_declaration(self, node: TSNode) -> None:  # adapt node type name
        self._handle_class(node, decorators=[])

    def _handle_class(self, node: TSNode, decorators: list[str]) -> None:
        name_node = next(
            (c for c in node.children if c.type == "identifier"), None
        )
        if name_node is None:
            return
        name = _node_text(name_node, self._source)
        qname = f"{self._scope_stack[-1]}.{name}"

        # Collect base class name tokens as "base" occurrences
        # (actual INHERITS_FROM edges are emitted by the resolution pass)
        for base_name_node in _iter_base_nodes(node):  # adapt to language
            self.occurrences.append(OccurrenceRef(
                role="base",
                file_path=self._ctx.file_path,
                line=base_name_node.start_point[0] + 1,
                col=base_name_node.start_point[1] + 1,
                enclosing_id=make_node_id(
                    self._ctx.project_name, qname, NodeKind.CLASS.value
                ),
            ))

        class_node = self._make_node(
            NodeKind.CLASS, qname, name, node,
            name_node=name_node,
            metadata={"decorators": decorators},
        )
        self._add_node_with_relation(class_node, RelationKind.DECLARES)

        self._push(qname, class_node.id, NodeKind.CLASS)
        body = next((c for c in node.children if c.type == "class_body"), None)  # adapt
        if body:
            self._visit_children(body)
        self._pop()

    # -------------------------------------------------------------------------
    # Function / method
    # -------------------------------------------------------------------------

    def _visit_function_declaration(self, node: TSNode) -> None:  # adapt node type name
        self._handle_function(node, decorators=[])

    def _handle_function(self, node: TSNode, decorators: list[str]) -> None:
        parent_kind = self._kind_stack[-1]
        kind = NodeKind.METHOD if parent_kind == NodeKind.CLASS else NodeKind.FUNCTION

        name_node = next(
            (c for c in node.children if c.type == "identifier"), None
        )
        if name_node is None:
            return
        name = _node_text(name_node, self._source)
        qname = f"{self._scope_stack[-1]}.{name}"

        func_node = self._make_node(
            kind, qname, name, node,
            name_node=name_node,
            metadata={"decorators": decorators},
        )
        self._add_node_with_relation(func_node, RelationKind.DECLARES)

        self._push(qname, func_node.id, kind)

        params_node = next(
            (c for c in node.children if c.type == "formal_parameters"), None  # adapt
        )
        if params_node:
            self._extract_parameters(params_node, func_node.id, qname)

        body = next((c for c in node.children if c.type == "statement_block"), None)  # adapt
        if body:
            self._collect_occurrences(body, func_node.id)
            for child in body.children:
                if child.type in ("function_declaration", "class_declaration"):  # adapt
                    self.visit(child)

        self._pop()

    # -------------------------------------------------------------------------
    # Imports
    # -------------------------------------------------------------------------

    def _visit_import_statement(self, node: TSNode) -> None:  # adapt node type name
        # Language-specific: parse the import syntax and call _emit_import()
        pass

    def _emit_import(
        self,
        *,
        local_name: str,
        ext_qname: str,
        is_relative: bool,
        alias: str | None = None,
        is_star: bool = False,
    ) -> None:
        top_level = ext_qname.split(".", maxsplit=1)[0]
        origin = (
            "internal" if is_relative
            else self._classifier.classify(top_level)
        )

        import_qname = f"{self._scope_stack[-1]}.{local_name}"
        import_node = self._make_node(
            NodeKind.IMPORT, import_qname, local_name,
            metadata={
                "alias": alias,
                "is_relative": is_relative,
                "original_name": ext_qname,
                "is_star": is_star,
                "origin": origin,
            },
        )
        self._add_node_with_relation(import_node, RelationKind.DECLARES)

        resolve_target_id: str | None = None
        if origin == "internal":
            resolve_target_id = _find_module_node_id(self._graph, ext_qname)

        if resolve_target_id is None:
            ext_sym = self._get_or_create_external_symbol(ext_qname, origin=origin)
            resolve_target_id = ext_sym.id

        self._graph.add_relation(Relation(
            source_id=self._file_node_id,
            target_id=resolve_target_id,
            kind=RelationKind.IMPORTS,
        ))
        self._graph.add_relation(Relation(
            source_id=import_node.id,
            target_id=resolve_target_id,
            kind=RelationKind.RESOLVES_TO,
        ))

    # -------------------------------------------------------------------------
    # Parameters
    # -------------------------------------------------------------------------

    def _extract_parameters(
        self, params_node: TSNode, function_id: str, function_qname: str
    ) -> None:
        for child in params_node.children:
            param_name: str | None = None
            annotation_node: TSNode | None = None
            has_default = False

            if child.type == "identifier":
                param_name = _node_text(child, self._source)
            # elif child.type == "required_parameter":  # TypeScript pattern
            #     ...
            # adapt to the language's parameter node types

            if not param_name:
                continue

            param_qname = f"{function_qname}.{param_name}"
            param_node = self._make_node(
                NodeKind.PARAMETER, param_qname, param_name, child,
                name_node=child,
                metadata={"has_default": has_default},
            )
            self._safe_add_node(param_node)
            self._graph.add_relation(Relation(
                source_id=function_id,
                target_id=param_node.id,
                kind=RelationKind.DECLARES,
            ))

            # Collect annotation occurrence if present
            if annotation_node is not None:
                self.occurrences.append(OccurrenceRef(
                    role="annotation",
                    file_path=self._ctx.file_path,
                    line=annotation_node.start_point[0] + 1,
                    col=annotation_node.start_point[1] + 1,
                    enclosing_id=param_node.id,
                ))

    # -------------------------------------------------------------------------
    # Occurrence collection (call/read/write/annotation use-sites)
    # -------------------------------------------------------------------------

    def _collect_occurrences(self, body: TSNode, enclosing_id: str) -> None:
        """Recursively scan body for use-site tokens and record OccurrenceRefs."""
        for node in _iter_nodes(body):  # depth-first helper
            if node.type == "call_expression":  # adapt
                callee = _callee_node(node)  # language-specific helper
                if callee is not None:
                    self.occurrences.append(OccurrenceRef(
                        role="call",
                        file_path=self._ctx.file_path,
                        line=callee.start_point[0] + 1,
                        col=callee.start_point[1] + 1,
                        enclosing_id=enclosing_id,
                    ))
            elif node.type == "identifier":  # adapt — read of a name
                self.occurrences.append(OccurrenceRef(
                    role="read",
                    file_path=self._ctx.file_path,
                    line=node.start_point[0] + 1,
                    col=node.start_point[1] + 1,
                    enclosing_id=enclosing_id,
                ))

    # -------------------------------------------------------------------------
    # Helpers (language-agnostic — copy verbatim)
    # -------------------------------------------------------------------------

    def _get_or_create_external_symbol(self, qname: str, origin: str = "unknown") -> Node:
        sym_id = make_node_id(self._ctx.project_name, qname, NodeKind.EXTERNAL_SYMBOL.value)
        if sym_id not in self._graph.nodes:
            self._graph.add_node(Node(
                id=sym_id,
                kind=NodeKind.EXTERNAL_SYMBOL,
                qualified_name=qname,
                name=qname.rsplit(".", maxsplit=1)[-1],
                metadata={"origin": origin},
            ))
        return self._graph.nodes[sym_id]

    def _add_node_with_relation(self, node: Node, rel_kind: RelationKind) -> None:
        self._safe_add_node(node)
        self._graph.add_relation(Relation(
            source_id=self._container_stack[-1],
            target_id=node.id,
            kind=rel_kind,
        ))

    def _safe_add_node(self, node: Node) -> None:
        if node.id not in self._graph.nodes:
            self._graph.add_node(node)

    def _make_node(
        self,
        kind: NodeKind,
        qualified_name: str,
        name: str,
        ts_node: TSNode | None = None,
        name_node: TSNode | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Node:
        meta = dict(metadata or {})
        if name_node is not None:
            # name_span is the Span of the *name token*, used by SpanIndex
            meta["name_span"] = _make_span(name_node)
        return Node(
            id=make_node_id(self._ctx.project_name, qualified_name, kind.value),
            kind=kind,
            qualified_name=qualified_name,
            name=name,
            file_path=str(self._ctx.file_path),
            span=_make_span(ts_node) if ts_node else None,
            metadata=meta,
        )

    def _push(self, qname: str, node_id: str, kind: NodeKind) -> None:
        self._scope_stack.append(qname)
        self._container_stack.append(node_id)
        self._kind_stack.append(kind)

    def _pop(self) -> None:
        self._scope_stack.pop()
        self._container_stack.pop()
        self._kind_stack.pop()
```

---

## `_resolver.py`

```python
"""{Language} SymbolResolver — wraps the type-aware engine."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from graphlens.contracts import ResolvedRef, SymbolResolver

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("graphlens_{lang}")


class {Lang}Resolver(SymbolResolver):
    """
    Type-aware resolver for {language}.

    Wraps <engine> and maps use-site positions to declaration locations.
    Never raises — all errors are caught and return None/[].
    """

    def prepare(self, project_root: Path, files: list[Path]) -> None:
        """Initialise the resolver engine for the given project."""
        try:
            # e.g. start LSP server subprocess, pre-open files, etc.
            pass
        except Exception:
            logger.debug("Resolver prepare failed", exc_info=True)

    def definition_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        """
        Return the definition of the symbol at (line, col) in file.
        line and col are 1-based.
        """
        try:
            # Call the engine's go-to-definition API
            # Return ResolvedRef(module_path=..., line=..., col=..., origin=...)
            pass
        except Exception:
            return None

    def infer_type_at(
        self, file: Path, line: int, col: int
    ) -> ResolvedRef | None:
        """Return the inferred type of the expression at (line, col)."""
        try:
            pass
        except Exception:
            return None

    def references_to(
        self, file: Path, line: int, col: int
    ) -> list[ResolvedRef]:
        """Return all references to the declaration at (line, col)."""
        try:
            return []
        except Exception:
            return []
```

---

## `_adapter.py`

```python
"""{Lang}Adapter — orchestrates {language} project analysis."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from graphlens import GraphLens, LanguageAdapter, Node, NodeKind, Relation, RelationKind
from graphlens.utils import make_node_id

from graphlens_{lang}._deps import {LANG}_DEFAULT_DEP_PARSERS, get_stdlib_names
from graphlens_{lang}._module_resolver import file_to_qualified_name, find_source_roots
from graphlens_{lang}._project_detector import (
    detect_project_name, find_{lang}_roots, is_{lang}_project,
)
from graphlens.utils import SpanIndex
from graphlens_{lang}._resolver import {Lang}Resolver
from graphlens_{lang}._visitor import (
    ImportClassifier, OccurrenceRef, {Lang}ASTVisitor, VisitorContext, parse_{lang},
)

if TYPE_CHECKING:
    from pathlib import Path
    from graphlens.contracts import DependencyFileParser

logger = logging.getLogger("graphlens_{lang}")
_STDLIB = get_stdlib_names()


class {Lang}Adapter(LanguageAdapter):
    """Language adapter for {language} projects."""

    def __init__(self, dep_parsers: list[DependencyFileParser] | None = None) -> None:
        self._dep_parsers = (
            dep_parsers if dep_parsers is not None
            else {LANG}_DEFAULT_DEP_PARSERS
        )

    def language(self) -> str:
        return "{lang}"

    def file_extensions(self) -> set[str]:
        return {"{ext}"}  # add all extensions

    def can_handle(self, project_root: Path) -> bool:
        return is_{lang}_project(project_root)

    def analyze(self, project_root: Path, files: list[Path] | None = None) -> GraphLens:
        graph = GraphLens()
        if files is not None:
            _analyze_root(graph, project_root, project_root, files, self._dep_parsers)
        else:
            for root in find_{lang}_roots(project_root):
                root_files = self.collect_files(root)
                _analyze_root(graph, project_root, root, root_files, self._dep_parsers)
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

    # Pre-pass: collect internal top-level names from file paths (no source parsing)
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

    project_id = make_node_id(project_name, project_name, NodeKind.PROJECT.value)
    if project_id not in graph.nodes:
        graph.add_node(Node(
            id=project_id,
            kind=NodeKind.PROJECT,
            qualified_name=project_name,
            name=project_name,
        ))

    modules: dict[str, str] = {}
    all_occurrences: list[OccurrenceRef] = []

    for file in files:
        source_root = _find_source_root_for(file, source_roots) or source_roots[0]
        try:
            module_qname = file_to_qualified_name(file, source_root)
        except ValueError:
            logger.warning("Cannot compute qualified name for %s, skipping", file)
            continue

        _ensure_module_chain(graph, project_name, module_qname, modules)

        try:
            relative_path = str(file.relative_to(project_root))
        except ValueError:
            relative_path = str(file.relative_to(lang_root))

        file_id = make_node_id(project_name, relative_path, NodeKind.FILE.value)
        if file_id not in graph.nodes:
            graph.add_node(Node(
                id=file_id,
                kind=NodeKind.FILE,
                qualified_name=relative_path,
                name=file.name,
                file_path=relative_path,
            ))
            leaf_module_id = modules[module_qname]
            graph.add_relation(Relation(
                source_id=leaf_module_id,
                target_id=file_id,
                kind=RelationKind.CONTAINS,
            ))

        try:
            source_bytes = file.read_bytes()
        except OSError as e:
            logger.warning("Cannot read %s: %s — skipping", file, e)
            continue

        tree = parse_{lang}(source_bytes)
        if tree.root_node.has_error:
            logger.warning("Parse errors in %s — continuing with partial results", file)

        ctx = VisitorContext(
            project_name=project_name,
            file_path=file,
            source_root=source_root,
            module_qualified_name=module_qname,
        )
        visitor = {Lang}ASTVisitor(ctx, graph, file_id, source_bytes, classifier)
        visitor.visit(tree.root_node)
        all_occurrences.extend(visitor.occurrences)

    # PROJECT --CONTAINS--> top-level modules
    top_level = {qn: mid for qn, mid in modules.items() if "." not in qn}
    for module_id in top_level.values():
        graph.add_relation(Relation(
            source_id=project_id,
            target_id=module_id,
            kind=RelationKind.CONTAINS,
        ))

    # ── Resolution pass ───────────────────────────────────────────────────────
    # After all files are visited: use SpanIndex + SymbolResolver to emit
    # CALLS / REFERENCES / HAS_TYPE / INHERITS_FROM edges that point to
    # real declaration nodes rather than EXTERNAL_SYMBOL placeholders.
    span_index = SpanIndex(graph)
    resolver = {Lang}Resolver()
    resolver.prepare(lang_root, files)

    _ROLE_TO_EDGE = {
        "call":       RelationKind.CALLS,
        "read":       RelationKind.REFERENCES,
        "write":      RelationKind.REFERENCES,
        "annotation": RelationKind.HAS_TYPE,
        "base":       RelationKind.INHERITS_FROM,
    }

    for occ in all_occurrences:
        edge_kind = _ROLE_TO_EDGE.get(occ.role)
        if edge_kind is None:
            continue
        ref = resolver.definition_at(occ.file_path, occ.line, occ.col)
        if ref is None:
            continue

        target_id: str | None = None
        if ref.module_path is not None:
            target_id = span_index.at(ref.module_path, ref.line, ref.col)

        if target_id is None:
            # Fallback: emit / reuse EXTERNAL_SYMBOL
            qname = ref.qualified_name or f"<unknown>:{occ.line}:{occ.col}"
            ext = _get_or_create_external_symbol(graph, project_name, qname, ref.origin or "unknown")
            target_id = ext.id

        if target_id != occ.enclosing_id:
            graph.add_relation(Relation(
                source_id=occ.enclosing_id,
                target_id=target_id,
                kind=edge_kind,
            ))


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
    """Ensure MODULE nodes exist for the full chain a.b.c. Returns leaf node ID."""
    parts = module_qname.split(".")
    parent_id: str | None = None
    for i in range(1, len(parts) + 1):
        qname = ".".join(parts[:i])
        if qname not in modules:
            node_id = make_node_id(project_name, qname, NodeKind.MODULE.value)
            graph.add_node(Node(
                id=node_id,
                kind=NodeKind.MODULE,
                qualified_name=qname,
                name=parts[i - 1],
            ))
            modules[qname] = node_id
            if parent_id is not None:
                graph.add_relation(Relation(
                    source_id=parent_id,
                    target_id=node_id,
                    kind=RelationKind.CONTAINS,
                ))
        parent_id = modules[qname]
    return modules[module_qname]
```

---

## `pyproject.toml`

```toml
[project]
name = "graphlens-{lang}"
version = "0.1.0"
description = "{language} language adapter for graphlens"
requires-python = ">=3.13"
dependencies = [
    "graphlens",
    "tree-sitter>=0.24",
    "tree-sitter-{lang}>=0.X",   # use the actual version constraint
]

[build-system]
requires = ["uv_build>=0.9.18,<0.12.0"]
build-backend = "uv_build"

[tool.uv.sources]
graphlens = { workspace = true }

[project.entry-points."graphlens.adapters"]
{lang} = "graphlens_{lang}:{Lang}Adapter"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.coverage.run]
source = ["graphlens", "graphlens_{lang}"]

[tool.coverage.report]
fail_under = 100
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "\\.\\.\\.",
]
```

---

## Tests

Mirror `packages/graphlens-python/tests/`. Tests are pure sync pytest with no containers.

### `tests/conftest.py`

```python
"""Shared test fixtures for graphlens-{lang}."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Return a temporary directory ready to receive {language} project files."""
    return tmp_path


def make_file(root: Path, rel_path: str, content: str = "") -> Path:
    """Create a file under root with the given content."""
    p = root / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p
```

### `tests/test_{lang}_project_detector.py`

```python
from pathlib import Path
from graphlens_{lang}._project_detector import (
    is_{lang}_project, find_{lang}_roots, detect_project_name,
)


def test_is_{lang}_project_with_marker(tmp_path: Path) -> None:
    (tmp_path / "{marker}").write_text("")
    assert is_{lang}_project(tmp_path)


def test_is_{lang}_project_fallback(tmp_path: Path) -> None:
    (tmp_path / "main{ext}").write_text("")
    assert is_{lang}_project(tmp_path)


def test_find_{lang}_roots_single(tmp_path: Path) -> None:
    (tmp_path / "{marker}").write_text("")
    assert find_{lang}_roots(tmp_path) == [tmp_path]


def test_detect_project_name_fallback(tmp_path: Path) -> None:
    assert detect_project_name(tmp_path) == tmp_path.name
```

### `tests/test_{lang}_adapter.py`

```python
from pathlib import Path
from graphlens import NodeKind, RelationKind
from graphlens_{lang} import {Lang}Adapter


def test_analyze_empty_project(tmp_path: Path) -> None:
    (tmp_path / "{marker}").write_text("")
    adapter = {Lang}Adapter()
    graph = adapter.analyze(tmp_path)
    assert graph is not None


def test_graph_has_project_node(tmp_path: Path) -> None:
    # Create a minimal source file
    src = tmp_path / "main{ext}"
    src.write_text("// empty")
    (tmp_path / "{marker}").write_text('{"name": "my-project"}')

    adapter = {Lang}Adapter()
    graph = adapter.analyze(tmp_path)

    project_nodes = [n for n in graph.nodes.values() if n.kind == NodeKind.PROJECT]
    assert len(project_nodes) == 1
    assert project_nodes[0].name == "my_project"  # normalized
```
