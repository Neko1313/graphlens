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
# Occurrence model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OccurrenceRef:
    """
    A lightweight record of a single name reference at a point in source.

    Roles:
    - ``"call"``       — the name is the callee of a call expression
    - ``"read"``       — the name is read (used as a value)
    - ``"write"``      — the name is written (assigned / declared)
    - ``"annotation"`` — the name appears as a type annotation
    - ``"base"``       — the name appears as a base class/interface
    """

    role: str
    line: int
    col: int
    enclosing_id: str
    span: Span

# ---------------------------------------------------------------------------
# Module-level singletons — one parser per grammar (ts / tsx)
# ---------------------------------------------------------------------------

_TS_LANGUAGE = Language(ts_typescript.language_typescript())
_TSX_LANGUAGE = Language(ts_typescript.language_tsx())

_ts_parser = Parser(_TS_LANGUAGE)
_tsx_parser = Parser(_TSX_LANGUAGE)

# Node types that can carry a method/property name in a class body
_METHOD_NAME_TYPES: frozenset[str] = frozenset(
    {
        "identifier",
        "property_identifier",
        "private_property_identifier",
    }
)


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
        # Occurrence model: collected name references
        self.occurrences: list[OccurrenceRef] = []
        self.abs_file_path: str = str(ctx.file_path)

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
        from_string = next((c for c in children if c.type == "string"), None)
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
                "type_alias_declaration",
                "enum_declaration",
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

    # -------------------------------------------------------------------------
    # Type alias
    # -------------------------------------------------------------------------

    def _visit_type_alias_declaration(self, node: TSNode) -> None:
        """
        Handle ``type Alias = SomeType;`` declarations.

        Creates a ``TYPE_ALIAS`` node and records an ``annotation``
        occurrence on the aliased type's leading identifier.
        """
        name_node = next(
            (c for c in node.children if c.type == "type_identifier"), None
        )
        if name_node is None:
            return
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        alias_node = self._make_node(
            NodeKind.TYPE_ALIAS,
            qname,
            name,
            node,
            metadata={},
            name_node=name_node,
        )
        self._add_node_with_relation(alias_node, RelationKind.DECLARES)

        # Record annotation occurrence on the aliased type
        # The aliased type is the named child after '='
        past_eq = False
        for child in node.children:
            if not child.is_named and child.type == "=":
                past_eq = True
                continue
            if past_eq and child.is_named:
                self._record_annotation(child, alias_node.id)
                break

    # -------------------------------------------------------------------------
    # Enum
    # -------------------------------------------------------------------------

    def _visit_enum_declaration(self, node: TSNode) -> None:
        """
        Handle ``enum E { A, B = 1 }`` declarations.

        Creates a ``CLASS`` node with ``metadata["is_enum"] = True`` and
        one ``ATTRIBUTE`` node per member.
        """
        name_node = next(
            (c for c in node.children if c.type == "identifier"), None
        )
        if name_node is None:
            return
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        enum_node = self._make_node(
            NodeKind.CLASS,
            qname,
            name,
            node,
            metadata={"is_enum": True},
            name_node=name_node,
        )
        self._add_node_with_relation(enum_node, RelationKind.DECLARES)

        self._push(qname, enum_node.id, NodeKind.CLASS)
        body = next(
            (c for c in node.children if c.type == "enum_body"), None
        )
        if body:
            for child in body.children:
                if child.type == "property_identifier":
                    # Bare member: E { A, B }
                    mem_name = _node_text(child)
                    mem_qname = f"{qname}.{mem_name}"
                    mem_node = self._make_node(
                        NodeKind.ATTRIBUTE,
                        mem_qname,
                        mem_name,
                        child,
                        metadata={},
                        name_node=child,
                    )
                    self._add_node_with_relation(
                        mem_node, RelationKind.DECLARES
                    )
                elif child.type == "enum_assignment":
                    # Member with value: E { B = 1 }
                    prop_node = next(
                        (
                            c for c in child.children
                            if c.type == "property_identifier"
                        ),
                        None,
                    )
                    if prop_node is None:
                        continue
                    mem_name = _node_text(prop_node)
                    mem_qname = f"{qname}.{mem_name}"
                    mem_node = self._make_node(
                        NodeKind.ATTRIBUTE,
                        mem_qname,
                        mem_name,
                        child,
                        metadata={},
                        name_node=prop_node,
                    )
                    self._add_node_with_relation(
                        mem_node, RelationKind.DECLARES
                    )
        self._pop()

    # -------------------------------------------------------------------------
    # Public field definition (class body)
    # -------------------------------------------------------------------------

    def _visit_public_field_definition(self, node: TSNode) -> None:
        """
        Handle a class-body field declaration (``x: T = value;``).

        Creates an ``ATTRIBUTE`` node and records a ``write`` occurrence.
        """
        prop_node = next(
            (c for c in node.children if c.type == "property_identifier"),
            None,
        )
        if prop_node is None:
            return
        name = _node_text(prop_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        attr_node = self._make_node(
            NodeKind.ATTRIBUTE,
            qname,
            name,
            node,
            metadata={},
            name_node=prop_node,
        )
        self._add_node_with_relation(attr_node, RelationKind.DECLARES)
        self._record_occurrence("write", prop_node, attr_node.id)

        # Scan the type annotation
        type_ann = next(
            (c for c in node.children if c.type == "type_annotation"), None
        )
        if type_ann is not None:
            self._record_annotation(type_ann, attr_node.id)

        # Scan the initializer
        init_value = next(
            (
                c for c in node.children
                if c.is_named
                and c.type not in (
                    "property_identifier",
                    "type_annotation",
                    "accessibility_modifier",
                )
                and c.type not in ("abstract", "readonly", "static",
                                   "override")
            ),
            None,
        )
        if init_value is not None:
            self._scan_value(init_value, attr_node.id)

    def _handle_class(
        self,
        node: TSNode,
        decorators: list[str],
        *,
        is_abstract: bool,
    ) -> None:
        name_node = next(
            (c for c in node.children if c.type == "type_identifier"), None
        ) or next((c for c in node.children if c.type == "identifier"), None)
        if name_node is None:
            return
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        # Extract base class from class_heritage → extends_clause
        bases: list[str] = []
        base_name_nodes: list[TSNode] = []
        heritage = next(
            (c for c in node.children if c.type == "class_heritage"), None
        )
        if heritage is not None:
            extends = next(
                (c for c in heritage.children if c.type == "extends_clause"),
                None,
            )
            if extends is not None:
                bases = _extract_heritage_bases(extends)
                base_name_nodes = _extract_heritage_base_nodes(extends)

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
            name_node=name_node,
        )
        self._add_node_with_relation(class_node, RelationKind.DECLARES)

        for base_name_node in base_name_nodes:
            self._record_occurrence("base", base_name_node, class_node.id)

        self._push(qname, class_node.id, NodeKind.CLASS)
        body = next((c for c in node.children if c.type == "class_body"), None)
        if body:
            self._visit_children(body)
        self._pop()

    # -------------------------------------------------------------------------
    # Interface (treated as CLASS with is_abstract=True)
    # -------------------------------------------------------------------------

    def _visit_interface_declaration(self, node: TSNode) -> None:
        name_node = next(
            (c for c in node.children if c.type == "type_identifier"), None
        ) or next((c for c in node.children if c.type == "identifier"), None)
        if name_node is None:
            return
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"

        # Interfaces may extend other interfaces
        bases: list[str] = []
        base_name_nodes: list[TSNode] = []
        extends_clause = next(
            (c for c in node.children if c.type == "extends_type_clause"), None
        )
        if extends_clause is not None:
            bases = _extract_heritage_bases(extends_clause)
            base_name_nodes = _extract_heritage_base_nodes(extends_clause)

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
            name_node=name_node,
        )
        self._add_node_with_relation(class_node, RelationKind.DECLARES)

        for base_name_node in base_name_nodes:
            self._record_occurrence("base", base_name_node, class_node.id)

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

    def _handle_function(self, node: TSNode, decorators: list[str]) -> None:
        is_async = any(c.type == "async" for c in node.children)
        parent_kind = self._kind_stack[-1]
        kind = (
            NodeKind.METHOD
            if parent_kind == NodeKind.CLASS
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
            name_node=name_node,
        )
        self._add_node_with_relation(func_node, RelationKind.DECLARES)

        # Record return type annotation occurrence
        if type_ann is not None:
            self._record_annotation(type_ann, func_node.id)

        self._push(qname, func_node.id, kind)

        # Parameters
        params_node = next(
            (c for c in node.children if c.type == "formal_parameters"), None
        )
        if params_node:
            self._extract_parameters(params_node, func_node.id, qname)

        # Body: walk statements (collects occurrences + visits nested defs)
        body = next(
            (c for c in node.children if c.type == "statement_block"), None
        )
        if body:
            self._walk_body(body, func_node.id)

        self._pop()

    # -------------------------------------------------------------------------
    # Arrow functions / const functions via lexical_declaration
    # -------------------------------------------------------------------------

    def _handle_lexical_declaration(self, node: TSNode) -> None:
        """
        Handle ``const/let`` declarations.

        Dispatches each declarator to either the function path (when the
        value is an arrow/function expression) or the variable path (all
        other values, including literals, objects, identifiers, calls).
        """
        is_const = any(c.type == "const" for c in node.children)
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = next(
                (c for c in declarator.children if c.type == "identifier"),
                None,
            )
            if name_node is None:
                continue

            _fn_types = ("arrow_function", "function", "function_expression")
            value_node = next(
                (c for c in declarator.children if c.type in _fn_types),
                None,
            )

            if value_node is not None:
                # ---- Function / method path --------------------------------
                self._handle_lexical_function(
                    declarator, name_node, value_node
                )
            else:
                # ---- Plain variable / attribute path -----------------------
                self._handle_lexical_variable(
                    declarator, name_node, is_const=is_const
                )

    def _handle_lexical_function(
        self,
        declarator: TSNode,
        name_node: TSNode,
        value_node: TSNode,
    ) -> None:
        """Build a FUNCTION/METHOD node from an arrow/function expression."""
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
            name_node=name_node,
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
            self._walk_body(body, func_node.id)

        self._pop()

    def _handle_lexical_variable(
        self,
        declarator: TSNode,
        name_node: TSNode,
        *,
        is_const: bool,
    ) -> None:
        """
        Build a VARIABLE/ATTRIBUTE node for a non-function declarator.

        A class-body declarator becomes an ``ATTRIBUTE``; otherwise a
        ``VARIABLE``. Records a ``write`` occurrence on the name token and
        scans the initializer via ``_scan_value`` for further reads/calls.
        """
        name = _node_text(name_node)
        qname = f"{self._scope_stack[-1]}.{name}"
        parent_kind = self._kind_stack[-1]
        var_kind = (
            NodeKind.ATTRIBUTE if parent_kind == NodeKind.CLASS
            else NodeKind.VARIABLE
        )
        var_node = self._make_node(
            var_kind,
            qname,
            name,
            declarator,
            metadata={"is_constant": is_const},
            name_node=name_node,
        )
        self._add_node_with_relation(var_node, RelationKind.DECLARES)
        # The write/read occurrences are enclosed by the current scope
        # (file, function, or class) — not by the variable node itself.
        enclosing_id = self._container_stack[-1]
        self._record_occurrence("write", name_node, enclosing_id)
        # Scan the initializer for reads/calls. The initializer is the named
        # child after the '=' operator: the second named child of the
        # declarator (the first named child is the binding identifier).
        named_children = [c for c in declarator.children if c.is_named]
        _init_index = 1
        if len(named_children) > _init_index:
            init_value = named_children[_init_index]
            self._scan_value(init_value, enclosing_id)

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
            identifiers = [c for c in spec.children if c.type == "identifier"]
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
            "internal" if is_relative else self._classifier.classify(top_level)
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
            type_node: TSNode | None = None
            has_default = False
            is_variadic = False

            if child.type == "identifier":
                param_name = _node_text(child)

            elif child.type == "required_parameter":
                # Check if this is actually a rest param (...args: T)
                rest_pat = next(
                    (c for c in child.children if c.type == "rest_pattern"),
                    None,
                )
                if rest_pat is not None:
                    id_node = next(
                        (
                            c
                            for c in rest_pat.children
                            if c.type == "identifier"
                        ),
                        None,
                    )
                    param_name = _node_text(id_node) if id_node else None
                    is_variadic = True
                else:
                    id_node = next(
                        (
                            c
                            for c in child.children
                            if c.type in ("identifier", "this")
                        ),
                        None,
                    )
                    param_name = _node_text(id_node) if id_node else None
                type_node = next(
                    (c for c in child.children if c.type == "type_annotation"),
                    None,
                )
                annotation = (
                    _node_text(type_node).lstrip(":").strip()
                    if type_node
                    else None
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
                    (c for c in child.children if c.type == "type_annotation"),
                    None,
                )
                annotation = (
                    _node_text(type_node).lstrip(":").strip()
                    if type_node
                    else None
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
            # Record type annotation occurrence
            if type_node is not None:
                self._record_annotation(type_node, function_id)

    # -------------------------------------------------------------------------
    # Occurrence model helpers
    # -------------------------------------------------------------------------

    def _first_identifier(self, node: TSNode) -> TSNode | None:  # noqa: PLR0911
        """
        Return the leading type identifier from a type annotation node.

        Handles ``type_annotation``, ``predefined_type``, ``type_identifier``,
        ``identifier``, ``generic_type``, and ``member_expression``.
        """
        if node.type == "type_annotation":
            # Strip the leading ':' token and recurse into the type
            for child in node.children:
                if child.is_named:
                    return self._first_identifier(child)
            return None
        if node.type in ("type_identifier", "identifier"):
            return node
        if node.type == "predefined_type":
            # Built-in: string, number, boolean, … — use the child token
            for child in node.children:
                return child
            return None
        if node.type == "generic_type":
            # Base<T> — extract just the base name
            return next(
                (
                    c for c in node.children
                    if c.type in ("type_identifier", "identifier")
                ),
                None,
            )
        if node.type == "member_expression":
            # NS.Type — use the trailing property
            return node.children[-1]
        # Recurse into first named child
        for child in node.children:
            if child.is_named:
                result = self._first_identifier(child)
                if result is not None:
                    return result
        return None

    def _record_annotation(
        self, type_ann_node: TSNode, enclosing_id: str
    ) -> None:
        """
        Record an ``annotation`` occurrence for the leading type name.

        ``type_ann_node`` is the ``type_annotation`` (or bare type) CST node;
        ``enclosing_id`` is the node ID of the enclosing function or class.
        """
        id_node = self._first_identifier(type_ann_node)
        if id_node is not None:
            self._record_occurrence("annotation", id_node, enclosing_id)

    # Node types that represent nested function/class definitions —
    # _scan_value must NOT descend into these.
    _NESTED_DEF_TYPES: frozenset[str] = frozenset({
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "arrow_function",
        "function",
        "function_expression",
        "method_definition",
    })

    def _record_occurrence(
        self, role: str, name_node: TSNode, enclosing_id: str
    ) -> None:
        """
        Append a single OccurrenceRef to ``self.occurrences``.

        ``role`` is one of ``call|read|write|annotation|base``; ``name_node``
        is the identifier CST node whose position is recorded; ``enclosing_id``
        is the node ID of the immediately enclosing function/class.
        """
        sp = _make_span(name_node)
        if sp is None:
            return
        self.occurrences.append(
            OccurrenceRef(
                role=role,
                line=sp.start_line,
                col=sp.start_col,
                enclosing_id=enclosing_id,
                span=sp,
            )
        )

    def _scan_value(self, node: TSNode, enclosing_id: str) -> None:  # noqa: PLR0912
        """
        Scan a value expression, collecting ``call``/``read`` occurrences.

        Rules:
        - ``call_expression`` → record ``call`` on the callee name (the
          trailing property for a ``member_expression`` callee), then scan
          ``arguments`` children for further reads.
        - ``identifier`` → record ``read``.
        - ``pair`` → scan the value child only (object literal).
        - ``shorthand_property_identifier`` → record ``read`` (it acts as
          both key and value in ``{ x }`` shorthand).
        - Nested function/class defs → do NOT recurse (scope boundary).
        - Everything else → recurse into children.
        """
        if node.type in self._NESTED_DEF_TYPES:
            return

        if node.type == "call_expression":
            callee = next(
                (c for c in node.children
                 if c.type in ("identifier", "member_expression")),
                None,
            )
            if callee is not None:
                if callee.type == "member_expression":
                    name_node = callee.children[-1]
                else:
                    name_node = callee
                self._record_occurrence("call", name_node, enclosing_id)
            # Scan arguments (not the callee itself — already recorded above)
            args = next(
                (c for c in node.children if c.type == "arguments"), None
            )
            if args is not None:
                for arg in args.children:
                    if arg.type in (",", "(", ")"):
                        continue
                    self._scan_value(arg, enclosing_id)
            return

        if node.type == "identifier":
            self._record_occurrence("read", node, enclosing_id)
            return

        if node.type == "pair":
            # Object literal { key: value } — scan only the value
            children = [c for c in node.children if c.is_named]
            _value_index = 1
            if len(children) > _value_index:
                self._scan_value(children[_value_index], enclosing_id)
            return

        if node.type == "shorthand_property_identifier":
            # { x } shorthand — identifier acts as both key and value
            self._record_occurrence("read", node, enclosing_id)
            return

        # Recurse into all other node types
        for child in node.children:
            self._scan_value(child, enclosing_id)

    def _walk_body(self, body: TSNode, enclosing_id: str) -> None:
        """Dispatch each statement of a ``statement_block`` body."""
        for child in body.children:
            self._walk_statement(child, enclosing_id)

    def _walk_statement(self, node: TSNode, enclosing_id: str) -> None:
        """
        Dispatch one statement node, scanning values for occurrences.

        Nested definitions are routed back through the main visitor; value
        expressions are scanned via ``_scan_value``.
        """
        t = node.type

        # Nested definitions — handled structurally by the main visitor
        if t in (
            "function_declaration",
            "generator_function_declaration",
            "class_declaration",
            "abstract_class_declaration",
            "interface_declaration",
            "export_statement",
        ):
            self.visit(node)
            return

        if t == "lexical_declaration":
            # Could be a const/let arrow function OR a plain variable
            self._handle_lexical_declaration(node)
            return

        if t == "return_statement":
            for child in node.children:
                if child.is_named:
                    self._scan_value(child, enclosing_id)
            return

        if t == "expression_statement":
            expr = next((c for c in node.children if c.is_named), None)
            if expr is not None:
                if expr.type in (
                    "assignment_expression",
                    "augmented_assignment_expression",
                ):
                    # Scan the right-hand side
                    rhs = expr.children[-1]
                    self._scan_value(rhs, enclosing_id)
                else:
                    self._scan_value(expr, enclosing_id)
            return

        # Blocks that may contain further statements
        if t in (
            "statement_block",
            "if_statement",
            "else_clause",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "try_statement",
            "catch_clause",
            "finally_clause",
            "switch_statement",
            "switch_body",
            "switch_case",
            "switch_default",
            "labeled_statement",
        ):
            for child in node.children:
                self._walk_statement(child, enclosing_id)
            return

        # Variable / expression sub-nodes — fall through to scan_value
        if node.is_named and t not in (
            "comment",
            "formal_parameters",
        ):
            self._scan_value(node, enclosing_id)

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

    def _make_node(  # noqa: PLR0913
        self,
        kind: NodeKind,
        qualified_name: str,
        name: str,
        ts_node: TSNode | None = None,
        metadata: dict[str, object] | None = None,
        name_node: TSNode | None = None,
    ) -> Node:
        """
        Build a graph Node, optionally recording a ``name_span``.

        When ``name_node`` is provided, its span is stored in
        ``metadata["name_span"]`` so a definition position can be mapped back
        to this node without scanning the node's full ``span``.
        """
        meta = dict(metadata) if metadata else {}
        if name_node is not None:
            ns = _make_span(name_node)
            if ns is not None:
                meta["name_span"] = ns
        return Node(
            id=make_node_id(
                self._ctx.project_name, qualified_name, kind.value
            ),
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
                    c
                    for c in child.children
                    if c.type in ("type_identifier", "identifier")
                ),
                None,
            )
            if name_node:
                bases.append(_node_text(name_node))
    return bases


def _extract_heritage_base_nodes(heritage_node: TSNode) -> list[TSNode]:
    """
    Return the name-bearing TSNode for each base in a heritage clause.

    Used by the occurrence model to record ``base`` occurrences with
    accurate source positions.
    """
    nodes: list[TSNode] = []
    for child in heritage_node.children:
        if child.type in ("identifier", "type_identifier"):
            nodes.append(child)
        elif child.type == "member_expression":
            # NS.Base — the trailing property identifier is the name token
            nodes.append(child.children[-1])
        elif child.type == "generic_type":
            name_node = next(
                (
                    c for c in child.children
                    if c.type in ("type_identifier", "identifier")
                ),
                None,
            )
            if name_node:
                nodes.append(name_node)
    return nodes


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
