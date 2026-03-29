"""TypeScript CST visitor — builds graphlens nodes/relations."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import tree_sitter_typescript as ts_typescript
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

from graphlens_typescript._module_resolver import resolve_relative_import

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("graphlens_typescript")

# ---------------------------------------------------------------------------
# Module-level singletons — one parser per grammar (ts / tsx)
# ---------------------------------------------------------------------------

_TS_LANGUAGE = Language(ts_typescript.language_typescript())
_TSX_LANGUAGE = Language(ts_typescript.language_tsx())

_ts_parser = Parser(_TS_LANGUAGE)
_tsx_parser = Parser(_TSX_LANGUAGE)

# Node types that can carry a method/property name in a class body
_METHOD_NAME_TYPES: frozenset[str] = frozenset({
    "identifier",
    "property_identifier",
    "private_property_identifier",
})


def parse_typescript(source: bytes, *, tsx: bool = False) -> object:
    """Parse TypeScript source bytes and return a tree-sitter Tree."""
    return _tsx_parser.parse(source) if tsx else _ts_parser.parse(source)


# ---------------------------------------------------------------------------
# Visitor context and import classifier
# ---------------------------------------------------------------------------


@dataclass
class ImportClassifier:
    """
    Classifies an import's origin based on pre-computed name sets.

    Origin values (stored in ``Node.metadata["origin"]``):
    - ``"stdlib"``      — Node.js standard library / built-ins
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
    """Context for one file's CST visit."""

    project_name: str
    file_path: Path
    file_relative_path: str
    source_root: Path
    module_qualified_name: str
    modules: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main visitor
# ---------------------------------------------------------------------------


class TypescriptASTVisitor:
    """
    Walks a tree-sitter TypeScript CST and populates a GraphLens.

    Node types handled:
      program, export_statement,
      class_declaration, abstract_class_declaration, interface_declaration,
      function_declaration, generator_function_declaration,
      method_definition,
      import_statement
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
        self._modules: dict[str, str] = ctx.modules
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
    # Root node
    # -------------------------------------------------------------------------

    def _visit_program(self, node: TSNode) -> None:
        self._visit_children(node)

    def _visit_lexical_declaration(self, node: TSNode) -> None:
        """Handle top-level ``const/let foo = () => ...`` declarations."""
        self._handle_lexical_declaration(node)

    # -------------------------------------------------------------------------
    # Export statement — unwrap and delegate to inner declaration
    # -------------------------------------------------------------------------

    def _visit_export_statement(self, node: TSNode) -> None:
        """
        Handle ``export`` statements.

        Cases:
        - ``export class/function/interface/abstract class`` → delegate
        - ``export default class/function`` → delegate
        - ``export { X, Y } from 'module'`` → re-export import
        - ``export { X, Y }`` (local re-export) → skip (no external dep)
        """
        children = node.children

        # Check for re-export with source: export { X } from 'module'
        # Note: tree-sitter-typescript exposes the string as a direct child
        # of export_statement, not wrapped in a from_clause node.
        export_clause = next(
            (c for c in children if c.type == "export_clause"), None
        )
        from_string = next(
            (c for c in children if c.type == "string"), None
        )
        if export_clause is not None and from_string is not None:
            module_path = _strip_string_quotes(_node_text(from_string))
            if module_path:
                self._emit_reexport(module_path)
            return

        # Unwrap exported declaration
        for child in children:
            if child.type in (
                "class_declaration",
                "abstract_class_declaration",
                "interface_declaration",
                "function_declaration",
                "generator_function_declaration",
            ):
                self.visit(child)
                return
            if child.type == "lexical_declaration":
                # export const/let Foo = ...
                self._handle_lexical_declaration(child)
                return

    def _emit_reexport(self, module_path: str) -> None:
        """Emit an IMPORT node for ``export { X } from 'module'``."""
        is_relative = module_path.startswith(("./", "../", "."))
        safe = module_path.replace("/", "_").replace(".", "_")
        local_name = f"__reexport_{safe}"
        self._emit_import(
            local_name=local_name,
            ext_qname=_module_path_to_qname(
                module_path,
                is_relative=is_relative,
                current_qname=self._scope_stack[-1],
            ),
            is_relative=is_relative,
            is_star=True,
        )

    # -------------------------------------------------------------------------
    # Class / abstract class
    # -------------------------------------------------------------------------

    def _visit_class_declaration(self, node: TSNode) -> None:
        self._handle_class(node, decorators=[], is_abstract=False)

    def _visit_abstract_class_declaration(self, node: TSNode) -> None:
        self._handle_class(node, decorators=[], is_abstract=True)

    def _handle_class(
        self,
        node: TSNode,
        decorators: list[str],
        *,
        is_abstract: bool,
    ) -> None:
        name_node = next(
            (c for c in node.children if c.type == "type_identifier"), None
        ) or next(
            (c for c in node.children if c.type == "identifier"), None
        )
        if name_node is None:
            return
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        # Extract base class from class_heritage → extends_clause
        bases: list[str] = []
        heritage = next(
            (c for c in node.children if c.type == "class_heritage"), None
        )
        if heritage is not None:
            extends = next(
                (
                    c for c in heritage.children
                    if c.type == "extends_clause"
                ),
                None,
            )
            if extends is not None:
                bases = _extract_heritage_bases(extends)

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
        body = next(
            (c for c in node.children if c.type == "class_body"), None
        )
        if body:
            self._visit_children(body)
        self._pop()

    # -------------------------------------------------------------------------
    # Interface (treated as CLASS with is_abstract=True)
    # -------------------------------------------------------------------------

    def _visit_interface_declaration(self, node: TSNode) -> None:
        name_node = next(
            (c for c in node.children if c.type == "type_identifier"), None
        ) or next(
            (c for c in node.children if c.type == "identifier"), None
        )
        if name_node is None:
            return
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        # Interfaces may extend other interfaces
        bases: list[str] = []
        extends_clause = next(
            (c for c in node.children if c.type == "extends_type_clause"), None
        )
        if extends_clause is not None:
            bases = _extract_heritage_bases(extends_clause)

        class_node = self._make_node(
            NodeKind.CLASS,
            qname,
            name,
            node,
            metadata={
                "decorators": [],
                "bases": bases,
                "is_abstract": True,
                "is_interface": True,
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
        body = next(
            (c for c in node.children if c.type == "interface_body"), None
        )
        if body:
            self._visit_children(body)
        self._pop()

    # -------------------------------------------------------------------------
    # Function / method
    # -------------------------------------------------------------------------

    def _visit_function_declaration(self, node: TSNode) -> None:
        self._handle_function(node, decorators=[])

    def _visit_generator_function_declaration(self, node: TSNode) -> None:
        self._handle_function(node, decorators=[])

    def _visit_method_definition(self, node: TSNode) -> None:
        """Handle method definitions inside class bodies."""
        self._handle_function(node, decorators=[])

    def _handle_function(
        self, node: TSNode, decorators: list[str]
    ) -> None:
        is_async = any(c.type == "async" for c in node.children)
        parent_kind = self._kind_stack[-1]
        kind = (
            NodeKind.METHOD if parent_kind == NodeKind.CLASS
            else NodeKind.FUNCTION
        )

        # For method_definition the name lives in a property_name slot
        # (identifier, property_identifier, or private_property_identifier).
        name_node = next(
            (c for c in node.children if c.type in _METHOD_NAME_TYPES),
            None,
        )
        if name_node is None:
            return
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        # Extract return type annotation if present
        return_annotation: str | None = None
        type_ann = next(
            (c for c in node.children if c.type == "type_annotation"), None
        )
        if type_ann is not None:
            return_annotation = _node_text(type_ann).lstrip(":").strip()

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
        params_node = next(
            (c for c in node.children if c.type == "formal_parameters"), None
        )
        if params_node:
            self._extract_parameters(params_node, func_node.id, qname)

        # Body: extract calls + visit nested definitions
        body = next(
            (c for c in node.children if c.type == "statement_block"), None
        )
        if body:
            self._extract_calls(body, func_node.id)
            for child in body.children:
                if child.type in (
                    "function_declaration",
                    "generator_function_declaration",
                    "class_declaration",
                    "abstract_class_declaration",
                    "interface_declaration",
                    "export_statement",
                    "lexical_declaration",
                ):
                    self.visit(child)

        self._pop()

    # -------------------------------------------------------------------------
    # Arrow functions / const functions via lexical_declaration
    # -------------------------------------------------------------------------

    def _handle_lexical_declaration(self, node: TSNode) -> None:
        """Handle ``const/let foo = () => ...`` declarations."""
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = next(
                (c for c in declarator.children if c.type == "identifier"),
                None,
            )
            _fn_types = ("arrow_function", "function", "function_expression")
            value_node = next(
                (c for c in declarator.children if c.type in _fn_types),
                None,
            )
            if name_node is None or value_node is None:
                continue
            name = _node_text(name_node)
            qname = f"{self._scope_stack[-1]}.{name}"

            is_async = any(c.type == "async" for c in value_node.children)
            parent_kind = self._kind_stack[-1]
            kind = (
                NodeKind.METHOD if parent_kind == NodeKind.CLASS
                else NodeKind.FUNCTION
            )

            return_annotation: str | None = None
            type_ann = next(
                (
                    c for c in declarator.children
                    if c.type == "type_annotation"
                ),
                None,
            )
            if type_ann is not None:
                return_annotation = _node_text(type_ann).lstrip(":").strip()

            func_node = self._make_node(
                kind,
                qname,
                name,
                value_node,
                metadata={
                    "decorators": [],
                    "is_async": is_async,
                    "return_annotation": return_annotation,
                },
            )
            self._add_node_with_relation(func_node, RelationKind.DECLARES)

            self._push(qname, func_node.id, kind)

            params_node = next(
                (
                    c for c in value_node.children
                    if c.type == "formal_parameters"
                ),
                None,
            )
            if params_node:
                self._extract_parameters(params_node, func_node.id, qname)

            body = next(
                (
                    c for c in value_node.children
                    if c.type == "statement_block"
                ),
                None,
            )
            if body:
                self._extract_calls(body, func_node.id)

            self._pop()

    # -------------------------------------------------------------------------
    # Import statement
    # -------------------------------------------------------------------------

    def _visit_import_statement(self, node: TSNode) -> None:
        """
        Handle TypeScript import statements.

        Covers:
        - ``import X from 'module'``           (default import)
        - ``import { A, B as C } from 'mod'``  (named imports)
        - ``import * as NS from 'mod'``         (namespace import)
        - ``import type { T } from 'mod'``      (type-only, same treatment)
        - ``import 'mod'``                      (side-effect import)
        """
        # Find the module path string
        from_clause = next(
            (c for c in node.children if c.type == "from_clause"), None
        )
        if from_clause is not None:
            module_path = _string_from_from_clause(from_clause)
        else:
            # Side-effect import: import 'module'
            str_node = next(
                (c for c in node.children if c.type == "string"), None
            )
            module_path = (
                _strip_string_quotes(_node_text(str_node)) if str_node else ""
            )

        if not module_path:
            return

        is_relative = module_path.startswith(("./", "../", "."))
        # Strip node: scheme prefix for stdlib classification
        classify_path = (
            module_path[5:] if module_path.startswith("node:") else module_path
        )
        ext_qname = _module_path_to_qname(
            classify_path,
            is_relative=is_relative,
            current_qname=self._scope_stack[-1],
        )

        # Find the import clause (may be absent for side-effect imports)
        import_clause = next(
            (c for c in node.children if c.type == "import_clause"), None
        )

        if import_clause is None:
            # Side-effect import — emit a single synthetic import
            self._emit_import(
                local_name=f"__sideeffect_{_path_to_safe_name(module_path)}",
                ext_qname=ext_qname,
                is_relative=is_relative,
                is_star=True,
            )
            return

        # Process each element of the import clause
        for child in import_clause.children:
            if child.type == "identifier":
                # Default import: import X from 'mod'
                local_name = _node_text(child)
                self._emit_import(
                    local_name=local_name,
                    ext_qname=f"{ext_qname}.default",
                    is_relative=is_relative,
                    alias=local_name,
                )

            elif child.type == "named_imports":
                self._process_named_imports(
                    child, ext_qname=ext_qname, is_relative=is_relative
                )

            elif child.type == "namespace_import":
                # Namespace import: import * as NS from 'mod'
                id_node = next(
                    (c for c in child.children if c.type == "identifier"),
                    None,
                )
                if id_node is not None:
                    local_name = _node_text(id_node)
                    self._emit_import(
                        local_name=local_name,
                        ext_qname=ext_qname,
                        is_relative=is_relative,
                        alias=local_name,
                        is_star=True,
                    )

    def _process_named_imports(
        self,
        named_imports_node: TSNode,
        *,
        ext_qname: str,
        is_relative: bool,
    ) -> None:
        """Emit IMPORT nodes for ``{ A, B as C }`` named-import clauses."""
        for spec in named_imports_node.children:
            if spec.type != "import_specifier":
                continue
            identifiers = [
                c for c in spec.children if c.type == "identifier"
            ]
            if not identifiers:
                continue
            if len(identifiers) == 1:
                iname = _node_text(identifiers[0])
                self._emit_import(
                    local_name=iname,
                    ext_qname=f"{ext_qname}.{iname}",
                    is_relative=is_relative,
                )
            else:
                # "original as alias"
                orig = _node_text(identifiers[0])
                alias = _node_text(identifiers[1])
                self._emit_import(
                    local_name=alias,
                    ext_qname=f"{ext_qname}.{orig}",
                    is_relative=is_relative,
                    alias=alias,
                )

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
            parts = ext_qname.split(".")
            for length in range(len(parts), 0, -1):
                candidate = ".".join(parts[:length])
                if candidate in self._modules:
                    resolve_target_id = self._modules[candidate]
                    break

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

            elif child.type == "required_parameter":
                # Check if this is actually a rest param (...args: T)
                rest_pat = next(
                    (
                        c for c in child.children
                        if c.type == "rest_pattern"
                    ),
                    None,
                )
                if rest_pat is not None:
                    id_node = next(
                        (
                            c for c in rest_pat.children
                            if c.type == "identifier"
                        ),
                        None,
                    )
                    param_name = _node_text(id_node) if id_node else None
                    is_variadic = True
                else:
                    id_node = next(
                        (
                            c for c in child.children
                            if c.type in ("identifier", "this")
                        ),
                        None,
                    )
                    param_name = _node_text(id_node) if id_node else None
                type_node = next(
                    (
                        c for c in child.children
                        if c.type == "type_annotation"
                    ),
                    None,
                )
                annotation = (
                    _node_text(type_node).lstrip(":").strip()
                    if type_node else None
                )
                # required_parameter can have an = initializer (default value)
                if any(c.type == "=" for c in child.children):
                    has_default = True

            elif child.type == "optional_parameter":
                id_node = next(
                    (c for c in child.children if c.type == "identifier"),
                    None,
                )
                param_name = _node_text(id_node) if id_node else None
                type_node = next(
                    (
                        c for c in child.children
                        if c.type == "type_annotation"
                    ),
                    None,
                )
                annotation = (
                    _node_text(type_node).lstrip(":").strip()
                    if type_node else None
                )
                has_default = True

            elif child.type == "rest_parameter":
                id_node = next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                param_name = _node_text(id_node) if id_node else None
                is_variadic = True

            elif child.type == "assignment_pattern":
                # Default value parameter: x = defaultVal
                id_node = next(
                    (c for c in child.children if c.type == "identifier"), None
                )
                param_name = _node_text(id_node) if id_node else None
                has_default = True

            if not param_name or param_name == "this":
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
        """Find all call expression nodes in body and emit CALLS relations."""
        for child in body.children:
            self._find_calls_in_node(child, caller_id)

    def _find_calls_in_node(self, node: TSNode, caller_id: str) -> None:
        if node.type == "call_expression":
            func_node = next(
                (
                    c for c in node.children
                    if c.type in ("identifier", "member_expression")
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
        # Don't recurse into nested definitions
        if node.type not in (
            "function_declaration",
            "generator_function_declaration",
            "class_declaration",
            "abstract_class_declaration",
            "method_definition",
            "arrow_function",
            "function",
            "function_expression",
        ):
            for child in node.children:
                self._find_calls_in_node(child, caller_id)

    # -------------------------------------------------------------------------
    # Node helpers (language-agnostic)
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
            file_path=self._ctx.file_relative_path,
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


def _strip_string_quotes(s: str) -> str:
    """Strip surrounding single or double quotes from a string literal."""
    if s and len(s) > 1 and s[0] in ("'", '"', "`") and s[-1] == s[0]:
        return s[1:-1]
    return s


def _string_from_from_clause(from_clause: TSNode) -> str:
    """Extract the unquoted module path from a ``from_clause`` node."""
    str_node = next(
        (c for c in from_clause.children if c.type == "string"), None
    )
    if str_node is None:
        return ""
    return _strip_string_quotes(_node_text(str_node))


def _module_path_to_qname(
    module_path: str,
    *,
    is_relative: bool,
    current_qname: str,
) -> str:
    """Convert a module path to a dotted qualified name for graph storage."""
    if is_relative:
        return resolve_relative_import(current_qname, module_path)
    # Absolute import: use the path as-is (slashes → dots for sub-paths)
    # Top-level package name is the first segment
    return module_path.replace("/", ".")


def _path_to_safe_name(path: str) -> str:
    """Convert a module path to a safe Python identifier."""
    return re.sub(r"[^a-zA-Z0-9]", "_", path).strip("_")


def _name_from_node(node: TSNode) -> str:
    """Extract a dotted name from identifier or member_expression nodes."""
    if node.type == "identifier":
        return _node_text(node)
    if node.type == "member_expression":
        # member_expression: object "." property
        obj_node = node.children[0]
        prop_node = node.children[-1]
        parent = _name_from_node(obj_node)
        prop = _node_text(prop_node)
        return f"{parent}.{prop}" if parent else prop
    return ""


def _extract_heritage_bases(heritage_node: TSNode) -> list[str]:
    """Extract base class / interface names from a heritage node."""
    bases: list[str] = []
    for child in heritage_node.children:
        if child.type in ("identifier", "type_identifier"):
            bases.append(_node_text(child))
        elif child.type == "member_expression":
            name = _name_from_node(child)
            if name:
                bases.append(name)
        elif child.type == "generic_type":
            # e.g. Base<T> — extract just the base name
            name_node = next(
                (
                    c for c in child.children
                    if c.type in ("type_identifier", "identifier")
                ),
                None,
            )
            if name_node:
                bases.append(_node_text(name_node))
    return bases



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
