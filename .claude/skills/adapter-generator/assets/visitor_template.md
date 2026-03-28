# `_visitor.py` Template

Replace all placeholders before using:
- `{lang}` → snake_case language name (e.g. `typescript`)
- `{Lang}` → PascalCase (e.g. `Typescript`)
- `{language}` → human-readable (e.g. `TypeScript`)

**IMPORTANT**: Before filling in the `_visit_*` handler methods, run the grammar
inspection snippet from SKILL.md Step 3 to identify the actual tree-sitter node
type names for this language. The `# adapt node type name` comments mark places
that must be changed.

```python
"""{language} CST visitor using tree-sitter — builds graphlens nodes/relations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import tree_sitter_{lang} as ts_{lang}
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

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("graphlens_{lang}")

# ---------------------------------------------------------------------------
# Module-level singleton (one parser per process)
# ---------------------------------------------------------------------------

_LANGUAGE = Language(ts_{lang}.language())
_parser = Parser(_LANGUAGE)


def parse_{lang}(source: bytes) -> object:
    """Parse {language} source bytes and return a tree-sitter Tree."""
    return _parser.parse(source)


# ---------------------------------------------------------------------------
# Visitor context and classifier
# ---------------------------------------------------------------------------


@dataclass
class ImportClassifier:
    """
    Classifies an import's origin based on pre-computed name sets.

    Origin values (stored in ``Node.metadata["origin"]``):
    - ``"stdlib"``      — {language} standard library / built-ins
    - ``"internal"``    — module declared within the same project
    - ``"third_party"`` — package listed in the project's dependency files
    - ``"unknown"``     — none of the above
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


class {Lang}ASTVisitor:
    """
    Walks a tree-sitter {language} CST and populates a GraphLens.

    Node types handled: (fill in after grammar inspection)
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
    # Top-level source node — just visit children
    # -------------------------------------------------------------------------

    # TODO: replace "source_file" with the actual top-level node type
    def _visit_source_file(self, node: TSNode) -> None:  # adapt node type name
        self._visit_children(node)

    # -------------------------------------------------------------------------
    # Class / struct / interface
    # -------------------------------------------------------------------------

    # TODO: add handlers for all class-like declaration node types
    def _visit_class_declaration(self, node: TSNode) -> None:  # adapt node type name
        self._handle_class(node, decorators=[])

    def _handle_class(self, node: TSNode, decorators: list[str]) -> None:
        name_node = next(
            (c for c in node.children if c.type == "identifier"), None  # adapt
        )
        if name_node is None:
            return
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        # Extract base classes / interfaces
        bases: list[str] = []
        # TODO: adapt to the language's superclass/interface syntax
        # e.g. for TypeScript: look for "class_heritage" node

        is_abstract = False
        # TODO: detect abstract classes if applicable

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
        # TODO: adapt body node type name
        body = next((c for c in node.children if c.type == "class_body"), None)
        if body:
            self._visit_children(body)
        self._pop()

    # -------------------------------------------------------------------------
    # Function / method declaration
    # -------------------------------------------------------------------------

    # TODO: add handlers for all function-like declaration node types
    def _visit_function_declaration(self, node: TSNode) -> None:  # adapt node type name
        self._handle_function(node, decorators=[])

    def _handle_function(self, node: TSNode, decorators: list[str]) -> None:
        is_async = any(c.type == "async" for c in node.children)  # adapt if needed
        parent_kind = self._kind_stack[-1]
        kind = (
            NodeKind.METHOD if parent_kind == NodeKind.CLASS
            else NodeKind.FUNCTION
        )

        name_node = next(
            (c for c in node.children if c.type == "identifier"), None  # adapt
        )
        if name_node is None:
            return
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        # TODO: extract return type annotation if the language supports it
        return_annotation: str | None = None

        func_node = self._make_node(
            kind,
            qname,
            name,
            node,
            metadata={
                "decorators": decorators,
                "is_async": is_async,
                "return_annotation": return_annotation,
            },
        )
        self._add_node_with_relation(func_node, RelationKind.DECLARES)

        self._push(qname, func_node.id, kind)

        # Parameters
        # TODO: adapt parameter container node type name
        params_node = next(
            (c for c in node.children if c.type == "formal_parameters"), None
        )
        if params_node:
            self._extract_parameters(params_node, func_node.id, qname)

        # Body: extract calls + visit nested definitions
        # TODO: adapt body node type name
        body = next(
            (c for c in node.children if c.type == "statement_block"), None
        )
        if body:
            self._extract_calls(body, func_node.id)
            # Visit nested class/function definitions only
            for child in body.children:
                if child.type in (
                    "function_declaration",
                    "class_declaration",
                    # TODO: add other nested definition node types
                ):
                    self.visit(child)

        self._pop()

    # -------------------------------------------------------------------------
    # Import statement
    # -------------------------------------------------------------------------

    # TODO: add handlers for all import node types (named, default, namespace, etc.)
    def _visit_import_statement(self, node: TSNode) -> None:  # adapt node type name
        # TODO: parse the import syntax for this language and call _emit_import()
        # For each imported name / path, call:
        #   self._emit_import(
        #       local_name=...,
        #       ext_qname=...,
        #       is_relative=...,
        #       alias=...,
        #       is_star=...,
        #   )
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
            NodeKind.IMPORT,
            import_qname,
            local_name,
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

            # TODO: add cases for typed, default, variadic parameters
            # Language-specific examples:
            #
            # TypeScript:
            # elif child.type == "required_parameter":
            #     id_node = next((c for c in child.children if c.type == "identifier"), None)
            #     param_name = _node_text(id_node) if id_node else None
            #     type_node = next((c for c in child.children if c.type == "type_annotation"), None)
            #     annotation = _node_text(type_node) if type_node else None
            #
            # Rust:
            # elif child.type == "parameter":
            #     id_node = next((c for c in child.children if c.type == "identifier"), None)
            #     param_name = _node_text(id_node) if id_node else None

            if not param_name:
                continue

            param_qname = f"{function_qname}.{param_name}"
            param_node = self._make_node(
                NodeKind.PARAMETER,
                param_qname,
                param_name,
                child,
                metadata={
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
        # TODO: adapt call expression node type name for the language
        if node.type == "call_expression":  # adapt node type name
            func_node = next(
                (
                    c for c in node.children
                    if c.type in ("identifier", "member_expression")  # adapt
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
            "function_declaration",
            "class_declaration",
            # TODO: add other definition node types to skip
        ):
            for child in node.children:
                self._find_calls_in_node(child, caller_id)

    # -------------------------------------------------------------------------
    # Node helpers (language-agnostic — copy verbatim)
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
# Module-level helpers (language-agnostic)
# ---------------------------------------------------------------------------


def _node_text(node: TSNode) -> str:
    return node.text.decode("utf-8")


def _name_from_node(node: TSNode) -> str:
    """Extract a dotted name from identifier or member expression nodes."""
    if node.type == "identifier":
        return _node_text(node)
    # TODO: adapt for the language's member access node type
    # Python uses "attribute", TypeScript/JS uses "member_expression"
    if node.type in ("attribute", "member_expression"):
        parent = _name_from_node(node.children[0])
        attr = _node_text(node.children[-1])
        return f"{parent}.{attr}" if parent else attr
    return ""


def _find_module_node_id(graph: GraphLens, qname: str) -> str | None:
    """
    Return the ID of a MODULE node matching qname or its longest prefix.

    Tries exact match first, then walks up the hierarchy so that
    `from mypackage.utils import Foo` resolves to the `mypackage.utils`
    MODULE even when Foo is not its own node yet.
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
```
