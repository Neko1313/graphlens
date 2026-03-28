"""Python CST visitor using tree-sitter — builds graphlens nodes/relations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import tree_sitter_python as tspython
from graphlens import (
    GraphLens,
    Node,
    NodeKind,
    Relation,
    RelationKind,
)
from graphlens.utils import Span, make_node_id
from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from graphlens_python._module_resolver import resolve_relative_import

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("graphlens_python")

_PY_LANGUAGE = Language(tspython.language())
_parser = Parser(_PY_LANGUAGE)


def parse_python(source: bytes) -> object:
    """Parse Python source bytes and return a tree-sitter Tree."""
    return _parser.parse(source)


# ---------------------------------------------------------------------------
# Visitor context
# ---------------------------------------------------------------------------


@dataclass
class ImportClassifier:
    """
    Classifies an import's origin based on pre-computed name sets.

    Origin values (stored in ``Node.metadata["origin"]``):
    - ``"stdlib"``      — Python standard library
    - ``"internal"``    — module declared within the same project
    - ``"third_party"`` — package listed in the project's dependency files
    - ``"unknown"``     — none of the above (may be a transitive dep or
      missing)
    """

    stdlib: frozenset[str] = field(default_factory=frozenset)
    third_party: frozenset[str] = field(default_factory=frozenset)
    internal: frozenset[str] = field(default_factory=frozenset)

    def classify(self, top_level: str) -> str:
        if top_level in self.stdlib:
            return "stdlib"
        if top_level in self.internal:
            return "internal"
        if top_level in self.third_party:
            return "third_party"
        return "unknown"


@dataclass
class VisitorContext:
    """Immutable context for one file's CST visit."""

    project_name: str
    file_path: Path
    source_root: Path
    module_qualified_name: str


# ---------------------------------------------------------------------------
# Main visitor
# ---------------------------------------------------------------------------


class PythonASTVisitor:
    """
    Walks a tree-sitter Python CST and populates a GraphLens.

    Node types handled:
      module, decorated_definition, class_definition,
      function_definition, import_statement, import_from_statement
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
        # Stack of qualified name prefixes (current scope)
        self._scope_stack: list[str] = [ctx.module_qualified_name]
        # Stack of node IDs for emitting CONTAINS/DECLARES relations
        self._container_stack: list[str] = [file_node_id]
        # Stack of NodeKind to know if we're inside a class
        self._kind_stack: list[NodeKind] = [NodeKind.FILE]

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
    # Top-level visitors
    # -------------------------------------------------------------------------

    def _visit_module(self, node: TSNode) -> None:
        self._visit_children(node)

    def _visit_decorated_definition(self, node: TSNode) -> None:
        decorators = [
            _decorator_name(c) for c in node.children if c.type == "decorator"
        ]
        inner = next(
            (
                c
                for c in node.children
                if c.type in ("class_definition", "function_definition")
            ),
            None,
        )
        if inner is None:
            return
        if inner.type == "class_definition":
            self._handle_class(inner, decorators)
        else:
            self._handle_function(inner, decorators)

    def _visit_class_definition(self, node: TSNode) -> None:
        self._handle_class(node, decorators=[])

    def _visit_function_definition(self, node: TSNode) -> None:
        self._handle_function(node, decorators=[])

    def _visit_import_statement(self, node: TSNode) -> None:
        # import X  /  import X.Y  /  import X as Y
        for child in node.children:
            if child.type == "dotted_name":
                name = _dotted_name(child)
                self._emit_import(
                    local_name=name,
                    ext_qname=name,
                    is_relative=False,
                )
            elif child.type == "aliased_import":
                name_node = next(
                    c for c in child.children if c.type == "dotted_name"
                )
                alias_node = next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                name = _dotted_name(name_node)
                local = _node_text(alias_node) if alias_node else name
                self._emit_import(
                    local_name=local,
                    ext_qname=name,
                    is_relative=False,
                    alias=local if alias_node else None,
                )

    def _visit_import_from_statement(self, node: TSNode) -> None:
        children = node.children

        # Determine source module and relative level
        # (only look before `import` keyword)
        level = 0
        source_module = ""
        for child in children:
            if child.type == "import":
                # everything after this is what's being imported
                break
            if child.type == "relative_import":
                prefix = next(
                    (
                        c for c in child.children
                        if c.type == "import_prefix"
                    ),
                    None,
                )
                if prefix:
                    level = _node_text(prefix).count(".")
                mod_node = next(
                    (
                        c for c in child.children
                        if c.type == "dotted_name"
                    ),
                    None,
                )
                mod_name = _dotted_name(mod_node) if mod_node else None
                source_module = resolve_relative_import(
                    self._ctx.module_qualified_name, level, mod_name
                )
            elif child.type == "dotted_name":
                source_module = _dotted_name(child)

        is_relative = level > 0

        # Collect imported names (after `import` keyword)
        past_import_kw = False
        for child in children:
            if child.type == "import":
                past_import_kw = True
                continue
            if not past_import_kw:
                continue

            if child.type == "dotted_name":
                imported = _dotted_name(child)
                ext_qname = (
                    f"{source_module}.{imported}"
                    if source_module else imported
                )
                self._emit_import(
                    local_name=imported,
                    ext_qname=ext_qname,
                    is_relative=is_relative,
                    level=level,
                )
            elif child.type == "aliased_import":
                name_node = next(
                    c for c in child.children if c.type == "dotted_name"
                )
                alias_node = next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                imported = _dotted_name(name_node)
                local = _node_text(alias_node) if alias_node else imported
                ext_qname = (
                    f"{source_module}.{imported}"
                    if source_module else imported
                )
                self._emit_import(
                    local_name=local,
                    ext_qname=ext_qname,
                    is_relative=is_relative,
                    level=level,
                    alias=local if alias_node else None,
                )
            elif child.type == "wildcard_import":
                ext_qname = f"{source_module}.*" if source_module else "*"
                self._emit_import(
                    local_name="*",
                    ext_qname=ext_qname,
                    is_relative=is_relative,
                    level=level,
                    is_star=True,
                )

    # -------------------------------------------------------------------------
    # Class and function handlers
    # -------------------------------------------------------------------------

    def _handle_class(self, node: TSNode, decorators: list[str]) -> None:
        name_node = next(
            (c for c in node.children if c.type == "identifier"), None
        )
        if name_node is None:
            return
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        # Extract base classes from argument_list
        bases: list[str] = []
        arg_list = next(
            (c for c in node.children if c.type == "argument_list"), None
        )
        if arg_list:
            for c in arg_list.children:
                base_name = _name_from_node(c)
                if base_name:
                    bases.append(base_name)

        is_abstract = "ABC" in bases or "ABCMeta" in bases
        class_node = self._make_node(
            NodeKind.CLASS,
            qname,
            name,
            node,
            metadata={
                "decorators": decorators,
                "bases": bases,
                "is_abstract": is_abstract,
            },
        )
        self._add_node_with_relation(class_node, RelationKind.DECLARES)

        # INHERITS_FROM
        for base_name in bases:
            sym = self._get_or_create_external_symbol(base_name)
            self._graph.add_relation(
                Relation(
                    source_id=class_node.id,
                    target_id=sym.id,
                    kind=RelationKind.INHERITS_FROM,
                )
            )

        self._push(qname, class_node.id, NodeKind.CLASS)
        body = next((c for c in node.children if c.type == "block"), None)
        if body:
            self._visit_children(body)
        self._pop()

    def _handle_function(self, node: TSNode, decorators: list[str]) -> None:
        is_async = any(c.type == "async" for c in node.children)
        parent_kind = self._kind_stack[-1]
        kind = (
            NodeKind.METHOD if parent_kind == NodeKind.CLASS
            else NodeKind.FUNCTION
        )

        name_node = next(
            (c for c in node.children if c.type == "identifier"), None
        )
        if name_node is None:
            return
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        # Return type annotation
        return_annotation: str | None = None
        type_node = next(
            (
                c
                for c in node.children
                if c.type == "type" and c != node.children[0]
            ),
            None,
        )
        if type_node:
            return_annotation = _node_text(type_node)

        func_node = self._make_node(
            kind,
            qname,
            name,
            node,
            metadata={
                "decorators": decorators,
                "is_async": is_async,
                "is_classmethod": "classmethod" in decorators,
                "is_staticmethod": "staticmethod" in decorators,
                "is_property": "property" in decorators,
                "return_annotation": return_annotation,
            },
        )
        self._add_node_with_relation(func_node, RelationKind.DECLARES)

        self._push(qname, func_node.id, kind)

        # Parameters
        params_node = next(
            (c for c in node.children if c.type == "parameters"), None
        )
        if params_node:
            self._extract_parameters(params_node, func_node.id, qname)

        # Body: extract calls + visit nested defs
        body = next((c for c in node.children if c.type == "block"), None)
        if body:
            self._extract_calls(body, func_node.id)
            # Visit nested class/function definitions
            for child in body.children:
                if child.type in (
                    "function_definition",
                    "class_definition",
                    "decorated_definition",
                ):
                    self.visit(child)

        self._pop()

    # -------------------------------------------------------------------------
    # Parameter extraction
    # -------------------------------------------------------------------------

    def _extract_parameters(
        self, params_node: TSNode, function_id: str, function_qname: str
    ) -> None:
        for child in params_node.children:
            param_name: str | None = None
            annotation: str | None = None
            has_default = False
            is_variadic = False

            if child.type == "identifier":
                param_name = _node_text(child)

            elif child.type == "default_parameter":
                id_node = next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                param_name = _node_text(id_node) if id_node else None
                has_default = True

            elif child.type == "typed_parameter":
                id_node = next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                param_name = _node_text(id_node) if id_node else None
                type_node = next(
                    (c for c in child.children if c.type == "type"), None
                )
                annotation = _node_text(type_node) if type_node else None

            elif child.type == "typed_default_parameter":
                id_node = next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                param_name = _node_text(id_node) if id_node else None
                type_node = next(
                    (c for c in child.children if c.type == "type"), None
                )
                annotation = _node_text(type_node) if type_node else None
                has_default = True

            elif child.type in {
                "list_splat_pattern", "dictionary_splat_pattern"
            }:
                id_node = next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                param_name = _node_text(id_node) if id_node else None
                is_variadic = True

            if not param_name:
                continue

            param_qname = f"{function_qname}.{param_name}"
            param_node = self._make_node(
                NodeKind.PARAMETER,
                param_qname,
                param_name,
                child,
                metadata={
                    "is_self": param_name == "self",
                    "is_cls": param_name == "cls",
                    "annotation": annotation,
                    "has_default": has_default,
                    "is_variadic": is_variadic,
                },
            )
            self._safe_add_node(param_node)
            self._graph.add_relation(
                Relation(
                    source_id=function_id,
                    target_id=param_node.id,
                    kind=RelationKind.DECLARES,
                )
            )

    # -------------------------------------------------------------------------
    # Call extraction
    # -------------------------------------------------------------------------

    def _extract_calls(self, body: TSNode, caller_id: str) -> None:
        """Find all call nodes in body and emit CALLS relations."""
        for child in body.children:
            self._find_calls_in_node(child, caller_id)

    def _find_calls_in_node(self, node: TSNode, caller_id: str) -> None:
        if node.type == "call":
            func_node = next(
                (
                    c
                    for c in node.children
                    if c.type in ("identifier", "attribute")
                ),
                None,
            )
            if func_node:
                callee_name = _name_from_node(func_node)
                if callee_name:
                    sym_id = make_node_id(
                        self._ctx.project_name,
                        callee_name,
                        NodeKind.SYMBOL.value,
                    )
                    if sym_id not in self._graph.nodes:
                        self._graph.add_node(
                            Node(
                                id=sym_id,
                                kind=NodeKind.SYMBOL,
                                qualified_name=callee_name,
                                name=callee_name.split(".")[-1],
                                span=_make_span(node),
                            )
                        )
                    self._graph.add_relation(
                        Relation(
                            source_id=caller_id,
                            target_id=sym_id,
                            kind=RelationKind.CALLS,
                        )
                    )
        # Don't recurse into nested function/class definitions
        if node.type not in (
            "function_definition",
            "class_definition",
            "decorated_definition",
        ):
            for child in node.children:
                self._find_calls_in_node(child, caller_id)

    # -------------------------------------------------------------------------
    # Import helper
    # -------------------------------------------------------------------------

    def _emit_import(  # noqa: PLR0913
        self,
        *,
        local_name: str,
        ext_qname: str,
        is_relative: bool,
        level: int = 0,
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
            NodeKind.IMPORT,
            import_qname,
            local_name,
            metadata={
                "alias": alias,
                "is_relative": is_relative,
                "level": level,
                "original_name": ext_qname,
                "is_star": is_star,
                "origin": origin,
            },
        )
        self._add_node_with_relation(import_node, RelationKind.DECLARES)

        # For internal imports: resolve to the MODULE node if it already
        # exists in the graph (it will if the module was processed before
        # this file). Otherwise fall back to EXTERNAL_SYMBOL so the edge
        # is never missing.
        resolve_target_id: str | None = None
        if origin == "internal":
            resolve_target_id = _find_module_node_id(self._graph, ext_qname)

        if resolve_target_id is None:
            ext_sym = self._get_or_create_external_symbol(
                ext_qname, origin=origin
            )
            resolve_target_id = ext_sym.id

        self._graph.add_relation(
            Relation(
                source_id=self._file_node_id,
                target_id=resolve_target_id,
                kind=RelationKind.IMPORTS,
            )
        )
        self._graph.add_relation(
            Relation(
                source_id=import_node.id,
                target_id=resolve_target_id,
                kind=RelationKind.RESOLVES_TO,
            )
        )

    # -------------------------------------------------------------------------
    # Node helpers
    # -------------------------------------------------------------------------

    def _get_or_create_external_symbol(
        self, qname: str, origin: str = "unknown"
    ) -> Node:
        sym_id = make_node_id(
            self._ctx.project_name, qname, NodeKind.EXTERNAL_SYMBOL.value
        )
        if sym_id not in self._graph.nodes:
            self._graph.add_node(
                Node(
                    id=sym_id,
                    kind=NodeKind.EXTERNAL_SYMBOL,
                    qualified_name=qname,
                    name=qname.rsplit(".", maxsplit=1)[-1],
                    metadata={"origin": origin},
                )
            )
        return self._graph.nodes[sym_id]

    def _add_node_with_relation(
        self, node: Node, rel_kind: RelationKind
    ) -> None:
        self._safe_add_node(node)
        self._graph.add_relation(
            Relation(
                source_id=self._container_stack[-1],
                target_id=node.id,
                kind=rel_kind,
            )
        )

    def _safe_add_node(self, node: Node) -> None:
        if node.id not in self._graph.nodes:
            self._graph.add_node(node)

    def _make_node(
        self,
        kind: NodeKind,
        qualified_name: str,
        name: str,
        ts_node: TSNode | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Node:
        return Node(
            id=make_node_id(
                self._ctx.project_name, qualified_name, kind.value
            ),
            kind=kind,
            qualified_name=qualified_name,
            name=name,
            file_path=str(self._ctx.file_path),
            span=_make_span(ts_node) if ts_node else None,
            metadata=metadata or {},
        )

    def _push(self, qname: str, node_id: str, kind: NodeKind) -> None:
        self._scope_stack.append(qname)
        self._container_stack.append(node_id)
        self._kind_stack.append(kind)

    def _pop(self) -> None:
        self._scope_stack.pop()
        self._container_stack.pop()
        self._kind_stack.pop()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _node_text(node: TSNode) -> str:
    return node.text.decode("utf-8")


def _dotted_name(node: TSNode) -> str:
    """Extract a dotted name string from a dotted_name node."""
    return "".join(_node_text(c) for c in node.children if c.type != ",")


def _name_from_node(node: TSNode) -> str:
    """Extract a dotted name string from identifier or attribute nodes."""
    if node.type == "identifier":
        return _node_text(node)
    if node.type == "attribute":
        parent = _name_from_node(node.children[0])
        attr = _node_text(node.children[-1])
        return f"{parent}.{attr}" if parent else attr
    return ""


def _decorator_name(decorator_node: TSNode) -> str:
    """Extract decorator name from a decorator node."""
    for child in decorator_node.children:
        if child.type in ("identifier", "attribute", "call"):
            name = _name_from_node(child)
            if name:
                return name
    return ""


def _find_module_node_id(graph: GraphLens, qname: str) -> str | None:
    """
    Return the ID of a MODULE node matching qname or its longest prefix.

    Tries exact match first (``mypackage.utils``), then walks up the
    hierarchy (``mypackage``) so that ``from mypackage.utils import Foo``
    resolves to the ``mypackage.utils`` MODULE even when Foo is not its
    own node yet.
    """
    parts = qname.split(".")
    for length in range(len(parts), 0, -1):
        candidate = ".".join(parts[:length])
        for node in graph.nodes.values():
            if (
                node.kind == NodeKind.MODULE
                and node.qualified_name == candidate
            ):
                return node.id
    return None


def _make_span(node: TSNode | None) -> Span | None:
    """Convert tree-sitter node positions to a Span (1-based)."""
    if node is None:
        return None
    try:
        sr, sc = node.start_point
        er, ec = node.end_point
        return Span(
            start_line=sr + 1,
            start_col=sc + 1,
            end_line=er + 1,
            end_col=ec + 1,
        )
    except Exception:
        return None
