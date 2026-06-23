"""PHP CST visitor using tree-sitter — builds graphlens nodes/relations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import tree_sitter_php as tsphp
from graphlens import (
    GraphLens,
    Node,
    NodeKind,
    Relation,
    RelationKind,
)
from graphlens.utils import Span, make_node_id
from tree_sitter import Language, Parser, Tree
from tree_sitter import Node as TSNode

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("graphlens_php")

_PHP_LANGUAGE = Language(tsphp.language_php())
_parser = Parser(_PHP_LANGUAGE)


def parse_php(source: bytes) -> Tree:
    """Parse PHP source bytes and return a tree-sitter Tree."""
    return _parser.parse(source)


def extract_namespace(root: TSNode) -> str:
    """
    Return the first declared namespace in a file (``""`` for global).

    PSR-4 projects declare exactly one namespace per file; we take the first
    ``namespace_definition`` as authoritative for the file's qualified-name
    prefix.
    """
    for child in root.children:
        if child.type == "namespace_definition":
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                return _node_text(name_node).strip("\\")
    return ""


# ---------------------------------------------------------------------------
# Occurrence reference (use-site record)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OccurrenceRef:
    """
    A use-site that the resolver will bind to a definition.

    Coordinates are 1-based (matching Span convention).

    Roles:
      ``call``       — call-site of a function/method/constructor
      ``read``       — property/constant/name read
      ``write``      — property assignment target
      ``annotation`` — parameter / return / property type
      ``base``       — class parent, implemented interface, or used trait
    """

    role: str
    line: int
    col: int
    enclosing_id: str
    span: Span


# ---------------------------------------------------------------------------
# Import classification
# ---------------------------------------------------------------------------


@dataclass
class ImportClassifier:
    """
    Classifies a ``use`` import's origin from pre-computed name sets.

    Origin values (stored in ``Node.metadata["origin"]``):
    - ``"stdlib"``      — a PHP built-in class (unqualified ``use``)
    - ``"internal"``    — a namespace declared within the project (PSR-4)
    - ``"third_party"`` — a Composer vendor's namespace
    - ``"unknown"``     — none of the above

    ``internal`` is matched case-sensitively on the namespace's top segment;
    ``third_party`` is matched against the lowercased segment (Composer
    vendors are lowercase). See ``_deps`` for why vendor prefixes are used.
    """

    stdlib: frozenset[str] = field(default_factory=frozenset)
    third_party: frozenset[str] = field(default_factory=frozenset)
    internal: frozenset[str] = field(default_factory=frozenset)

    def classify(self, top_level: str, *, is_single: bool) -> str:
        if top_level in self.internal:
            return "internal"
        if top_level.lower() in self.third_party:
            return "third_party"
        if is_single and top_level in self.stdlib:
            return "stdlib"
        return "unknown"


@dataclass
class VisitorContext:
    """Immutable context for one file's CST visit."""

    project_name: str
    file_path: Path
    namespace: str


# ---------------------------------------------------------------------------
# Main visitor
# ---------------------------------------------------------------------------


class PhpASTVisitor:
    """
    Walks a tree-sitter PHP CST and populates a GraphLens.

    Structural declarations (classes, interfaces, traits, enums, functions,
    methods, properties, constants, parameters, imports) become nodes with
    ``DECLARES``/``IMPORTS``/``RESOLVES_TO`` edges. Use-sites (calls, type
    references, base classes, property reads/writes) are collected as
    :class:`OccurrenceRef` for the post-visit resolution pass — this visitor
    never emits ``CALLS``/``REFERENCES``/``HAS_TYPE``/``INHERITS_FROM``.
    """

    _NESTED_DEF_TYPES = (
        "class_declaration",
        "interface_declaration",
        "trait_declaration",
        "enum_declaration",
        "function_definition",
    )

    def __init__(  # noqa: PLR0913
        self,
        ctx: VisitorContext,
        graph: GraphLens,
        file_node_id: str,
        source: bytes,
        classifier: ImportClassifier | None = None,
        modules: dict[str, str] | None = None,
    ) -> None:
        self._ctx = ctx
        self._graph = graph
        self._file_node_id = file_node_id
        self._source = source
        self._classifier = classifier or ImportClassifier()
        # Shared namespace-qualified-name → MODULE node id index, populated by
        # the adapter as files are processed. Used to resolve internal imports
        # to their MODULE node by longest-prefix without scanning the graph.
        self._modules = modules if modules is not None else {}
        # Stack of qualified-name prefixes (current scope); "" = global ns
        self._scope_stack: list[str] = [ctx.namespace]
        # Stack of node IDs for emitting DECLARES relations
        self._container_stack: list[str] = [file_node_id]
        # Stack of NodeKind to know if we are inside a class
        self._kind_stack: list[NodeKind] = [NodeKind.FILE]
        # Occurrence use-sites collected during this visit
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

    def _visit_namespace_definition(self, node: TSNode) -> None:
        # Scope is already seeded from the file's namespace; descend so the
        # block form ``namespace X { ... }`` has its body processed too.
        self._visit_children(node)

    # -------------------------------------------------------------------------
    # Declarations
    # -------------------------------------------------------------------------

    def _visit_class_declaration(self, node: TSNode) -> None:
        self._handle_class(node, is_abstract=_has_abstract(node))

    def _visit_interface_declaration(self, node: TSNode) -> None:
        self._handle_class(node, is_interface=True)

    def _visit_trait_declaration(self, node: TSNode) -> None:
        self._handle_class(node, is_trait=True)

    def _visit_enum_declaration(self, node: TSNode) -> None:
        self._handle_class(node, is_enum=True)

    def _handle_class(
        self,
        node: TSNode,
        *,
        is_interface: bool = False,
        is_trait: bool = False,
        is_enum: bool = False,
        is_abstract: bool = False,
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:  # pragma: no cover - defensive
            return
        name = _node_text(name_node)
        qname = self._qualify(name)

        class_node = self._make_node(
            NodeKind.CLASS,
            qname,
            name,
            node,
            metadata={
                "is_interface": is_interface,
                "is_trait": is_trait,
                "is_enum": is_enum,
                "is_abstract": is_abstract,
            },
            name_node=name_node,
        )
        self._add_node_with_relation(class_node, RelationKind.DECLARES)

        # Base classes / interfaces (extends + implements)
        for clause_type in ("base_clause", "class_interface_clause"):
            clause = next(
                (c for c in node.children if c.type == clause_type), None
            )
            if clause is not None:
                for ref in _type_refs(clause):
                    self._record_occurrence("base", ref, class_node.id)

        self._push(qname, class_node.id, NodeKind.CLASS)
        body = node.child_by_field_name("body")
        if body is not None:  # pragma: no cover - classes always have a body
            self._visit_children(body)
        self._pop()

    def _visit_use_declaration(self, node: TSNode) -> None:
        """Trait use inside a class body — modelled as a ``base`` edge."""
        for ref in _type_refs(node):
            self._record_occurrence("base", ref, self._container_stack[-1])

    def _visit_function_definition(self, node: TSNode) -> None:
        self._handle_function(node)

    def _visit_method_declaration(self, node: TSNode) -> None:
        self._handle_function(node)

    def _handle_function(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:  # pragma: no cover - defensive
            return
        name = _node_text(name_node)
        qname = self._qualify(name)
        kind = (
            NodeKind.METHOD
            if self._kind_stack[-1] == NodeKind.CLASS
            else NodeKind.FUNCTION
        )

        func_node = self._make_node(
            kind,
            qname,
            name,
            node,
            metadata={
                "is_static": _has_modifier(node, "static_modifier"),
                "is_abstract": _has_abstract(node),
                "visibility": _visibility(node),
            },
            name_node=name_node,
        )
        self._add_node_with_relation(func_node, RelationKind.DECLARES)

        return_type = node.child_by_field_name("return_type")
        if return_type is not None:
            self._record_type(return_type, func_node.id)

        self._push(qname, func_node.id, kind)
        params = node.child_by_field_name("parameters")
        if params is not None:  # pragma: no cover - always present
            self._extract_parameters(params, func_node.id, qname)
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk_body(body, func_node.id)
        self._pop()

    def _extract_parameters(
        self, params_node: TSNode, function_id: str, function_qname: str
    ) -> None:
        for child in params_node.children:
            if child.type not in (
                "simple_parameter",
                "variadic_parameter",
                "property_promotion_parameter",
            ):
                continue
            var_node = child.child_by_field_name("name")
            id_node = _name_child(var_node) if var_node is not None else None
            if id_node is None:  # pragma: no cover - defensive
                continue
            param_name = _node_text(id_node)
            type_node = child.child_by_field_name("type")
            is_promoted = child.type == "property_promotion_parameter"

            param_node = self._make_node(
                NodeKind.PARAMETER,
                f"{function_qname}\\{param_name}",
                param_name,
                child,
                metadata={
                    "is_variadic": child.type == "variadic_parameter",
                    "is_promoted": is_promoted,
                    "has_default": child.child_by_field_name("default_value")
                    is not None,
                },
                name_node=id_node,
            )
            self._safe_add_node(param_node)
            self._graph.add_relation(
                Relation(
                    source_id=function_id,
                    target_id=param_node.id,
                    kind=RelationKind.DECLARES,
                )
            )
            if type_node is not None:
                self._record_type(type_node, param_node.id)

    def _visit_property_declaration(self, node: TSNode) -> None:
        type_node = node.child_by_field_name("type")
        for element in node.children:
            if element.type != "property_element":
                continue
            var_node = element.child_by_field_name("name")
            id_node = _name_child(var_node) if var_node is not None else None
            if id_node is None:  # pragma: no cover - defensive
                continue
            name = _node_text(id_node)
            prop_node = self._make_node(
                NodeKind.ATTRIBUTE,
                self._qualify(name),
                name,
                element,
                metadata={"visibility": _visibility(node)},
                name_node=id_node,
            )
            self._add_node_with_relation(prop_node, RelationKind.DECLARES)
            if type_node is not None:
                self._record_type(type_node, prop_node.id)

    def _visit_const_declaration(self, node: TSNode) -> None:
        in_class = self._kind_stack[-1] == NodeKind.CLASS
        kind = NodeKind.ATTRIBUTE if in_class else NodeKind.VARIABLE
        for element in node.children:
            if element.type != "const_element":
                continue
            name_node = next(
                (c for c in element.children if c.type == "name"), None
            )
            if name_node is None:  # pragma: no cover - defensive
                continue
            name = _node_text(name_node)
            const_node = self._make_node(
                kind,
                self._qualify(name),
                name,
                element,
                metadata={"is_constant": True},
                name_node=name_node,
            )
            self._add_node_with_relation(const_node, RelationKind.DECLARES)

    def _visit_enum_case(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:  # pragma: no cover - defensive
            return
        name = _node_text(name_node)
        case_node = self._make_node(
            NodeKind.ATTRIBUTE,
            self._qualify(name),
            name,
            node,
            metadata={"is_enum_case": True},
            name_node=name_node,
        )
        self._add_node_with_relation(case_node, RelationKind.DECLARES)

    # -------------------------------------------------------------------------
    # Imports
    # -------------------------------------------------------------------------

    def _visit_namespace_use_declaration(self, node: TSNode) -> None:
        group = next(
            (c for c in node.children if c.type == "namespace_use_group"),
            None,
        )
        if group is not None:
            prefix_node = next(
                (c for c in node.children if c.type == "namespace_name"), None
            )
            prefix = _node_text(prefix_node) if prefix_node else ""
            for clause in group.children:
                if clause.type == "namespace_use_clause":
                    self._emit_use_clause(clause, prefix)
            return
        for clause in node.children:
            if clause.type == "namespace_use_clause":
                self._emit_use_clause(clause, "")

    def _emit_use_clause(self, clause: TSNode, prefix: str) -> None:
        path_node = next(
            (
                c
                for c in clause.children
                if c.type in ("qualified_name", "name")
            ),
            None,
        )
        if path_node is None:  # pragma: no cover - defensive
            return
        path = _node_text(path_node).strip("\\")
        ext_qname = f"{prefix}\\{path}" if prefix else path
        ext_qname = ext_qname.strip("\\")
        alias_node = clause.child_by_field_name("alias")
        local = (
            _node_text(alias_node)
            if alias_node is not None
            else ext_qname.rsplit("\\", maxsplit=1)[-1]
        )
        self._emit_import(local_name=local, ext_qname=ext_qname)

    def _emit_import(self, *, local_name: str, ext_qname: str) -> None:
        top = ext_qname.split("\\", maxsplit=1)[0]
        is_single = "\\" not in ext_qname
        origin = self._classifier.classify(top, is_single=is_single)

        import_node = self._make_node(
            NodeKind.IMPORT,
            self._qualify(local_name),
            local_name,
            metadata={
                "alias": local_name
                if local_name != ext_qname.rsplit("\\", maxsplit=1)[-1]
                else None,
                "original_name": ext_qname,
                "origin": origin,
            },
        )
        self._add_node_with_relation(import_node, RelationKind.DECLARES)

        target_id: str | None = None
        if origin == "internal":
            target_id = self._lookup_module(ext_qname)
        if target_id is None:
            target_id = self._get_or_create_external_symbol(
                ext_qname, origin=origin
            ).id

        self._graph.add_relation(
            Relation(
                source_id=self._file_node_id,
                target_id=target_id,
                kind=RelationKind.IMPORTS,
            )
        )
        self._graph.add_relation(
            Relation(
                source_id=import_node.id,
                target_id=target_id,
                kind=RelationKind.RESOLVES_TO,
            )
        )

    # -------------------------------------------------------------------------
    # Value scanning (calls / reads / writes)
    # -------------------------------------------------------------------------

    def _visit_expression_statement(self, node: TSNode) -> None:
        """Scan top-level / namespace-scope statements for use-sites."""
        for child in node.children:
            self._scan_value(child, self._container_stack[-1])

    def _walk_body(self, body: TSNode, enclosing_id: str) -> None:
        """Walk a function/method body, recording use-sites once each."""
        for child in body.children:
            if child.type in self._NESTED_DEF_TYPES:
                self.visit(child)
            else:
                self._scan_value(child, enclosing_id)

    def _scan_value(self, node: TSNode, enclosing_id: str) -> None:  # noqa: PLR0911, PLR0912
        """Record ``call``/``read``/``write`` occurrences in an expression."""
        t = node.type
        if t == "function_call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:  # pragma: no cover - always present
                callee = _callee_name(fn)
                if callee is not None:
                    self._record_occurrence("call", callee, enclosing_id)
                elif fn.type not in ("name", "qualified_name"):
                    self._scan_value(fn, enclosing_id)
            self._scan_arguments(node, enclosing_id)
            return
        if t in (
            "member_call_expression",
            "nullsafe_member_call_expression",
            "scoped_call_expression",
        ):
            name_node = node.child_by_field_name("name")
            if name_node is not None and name_node.type == "name":
                self._record_occurrence("call", name_node, enclosing_id)
            obj = node.child_by_field_name("object")
            if obj is not None:
                self._scan_value(obj, enclosing_id)
            self._scan_arguments(node, enclosing_id)
            return
        if t == "object_creation_expression":
            cls = _object_creation_class(node)
            if cls is not None:
                callee = _callee_name(cls)
                if callee is not None:  # pragma: no cover - always a name
                    self._record_occurrence("call", callee, enclosing_id)
            self._scan_arguments(node, enclosing_id)
            return
        if t in (
            "member_access_expression",
            "nullsafe_member_access_expression",
        ):
            name_node = node.child_by_field_name("name")
            if name_node is not None and name_node.type == "name":
                self._record_occurrence("read", name_node, enclosing_id)
            obj = node.child_by_field_name("object")
            if obj is not None:  # pragma: no cover - always present
                self._scan_value(obj, enclosing_id)
            return
        if t == "class_constant_access_expression":
            # The trailing name leaf is the constant (or ``class``) reference.
            self._record_occurrence("read", node.children[-1], enclosing_id)
            return
        if t == "assignment_expression":
            self._scan_assignment(node, enclosing_id)
            return
        if t == "name":
            self._record_occurrence("read", node, enclosing_id)
            return
        if t == "qualified_name":
            last = _last_name(node)
            if last is not None:  # pragma: no cover - always has a name leaf
                self._record_occurrence("read", last, enclosing_id)
            return
        if t == "variable_name":
            return  # local variable — not resolvable across files
        for child in node.children:
            self._scan_value(child, enclosing_id)

    def _scan_assignment(self, node: TSNode, enclosing_id: str) -> None:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is not None and left.type in (
            "member_access_expression",
            "nullsafe_member_access_expression",
        ):
            name_node = left.child_by_field_name("name")
            if name_node is not None and name_node.type == "name":
                self._record_occurrence("write", name_node, enclosing_id)
            obj = left.child_by_field_name("object")
            if obj is not None:  # pragma: no cover - always present
                self._scan_value(obj, enclosing_id)
        elif left is not None:  # pragma: no cover - always present
            self._scan_value(left, enclosing_id)
        if right is not None:  # pragma: no cover - always present
            self._scan_value(right, enclosing_id)

    def _scan_arguments(self, node: TSNode, enclosing_id: str) -> None:
        args = node.child_by_field_name("arguments")
        if args is None:
            return
        for arg in args.children:
            if arg.type == "argument":
                for child in arg.children:
                    self._scan_value(child, enclosing_id)

    def _record_type(self, type_node: TSNode, enclosing_id: str) -> None:
        for ref in _type_refs(type_node):
            self._record_occurrence("annotation", ref, enclosing_id)

    def _record_occurrence(
        self, role: str, name_node: TSNode, enclosing_id: str
    ) -> None:
        span = _make_span(name_node)
        if span is None:  # pragma: no cover - defensive
            return
        self.occurrences.append(
            OccurrenceRef(
                role=role,
                line=span.start_line,
                col=span.start_col,
                enclosing_id=enclosing_id,
                span=span,
            )
        )

    # -------------------------------------------------------------------------
    # Node helpers
    # -------------------------------------------------------------------------

    def _qualify(self, name: str) -> str:
        scope = self._scope_stack[-1]
        return f"{scope}\\{name}" if scope else name

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
                    name=qname.rsplit("\\", maxsplit=1)[-1],
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
        md = dict(metadata or {})
        if name_node is not None:
            name_span = _make_span(name_node)
            if name_span is not None:  # pragma: no cover - always valid here
                md["name_span"] = name_span
        return Node(
            id=make_node_id(
                self._ctx.project_name, qualified_name, kind.value
            ),
            kind=kind,
            qualified_name=qualified_name,
            name=name,
            file_path=str(self._ctx.file_path),
            span=_make_span(ts_node) if ts_node else None,
            metadata=md,
        )

    def _lookup_module(self, qname: str) -> str | None:
        r"""
        Return the MODULE id for ``qname`` or its longest namespace prefix.

        ``App\\Model\\User`` resolves to the ``App\\Model`` namespace MODULE
        even when the ``User`` class is not yet its own node. Uses the shared
        ``modules`` index (O(depth) lookups) rather than scanning the graph.
        """
        parts = qname.split("\\")
        for length in range(len(parts), 0, -1):
            candidate = "\\".join(parts[:length])
            module_id = self._modules.get(candidate)
            if module_id is not None:
                return module_id
        return None

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
    return node.text.decode("utf-8") if node.text is not None else ""


def _name_child(node: TSNode) -> TSNode | None:
    """Return the ``name`` token of a ``variable_name`` (``$x`` → ``x``)."""
    return next((c for c in node.children if c.type == "name"), None)


def _last_name(node: TSNode) -> TSNode | None:
    """Return the last ``name`` leaf of a qualified name."""
    result: TSNode | None = None
    for child in node.children:
        if child.type == "name":
            result = child
    return result


def _callee_name(node: TSNode) -> TSNode | None:
    """Return the name token identifying a called function/class."""
    if node.type == "name":
        return node
    if node.type == "qualified_name":
        return _last_name(node)
    return None


def _object_creation_class(node: TSNode) -> TSNode | None:
    """Return the class node of a ``new X(...)`` expression."""
    for child in node.children:
        if child.type in ("name", "qualified_name"):
            return child
    return None


def _type_refs(node: TSNode) -> list[TSNode]:
    """Collect class-name leaves from a type or heritage clause."""
    out: list[TSNode] = []
    _collect_type_refs(node, out)
    return out


def _collect_type_refs(node: TSNode, out: list[TSNode]) -> None:
    t = node.type
    if t in ("primitive_type", "null", "bottom_type"):
        return
    if t == "qualified_name":
        last = _last_name(node)
        if last is not None:  # pragma: no cover - always has a name leaf
            out.append(last)
        return
    if t == "name":
        out.append(node)
        return
    for child in node.children:
        _collect_type_refs(child, out)


def _has_modifier(node: TSNode, modifier_type: str) -> bool:
    return any(c.type == modifier_type for c in node.children)


def _has_abstract(node: TSNode) -> bool:
    return _has_modifier(node, "abstract_modifier")


def _visibility(node: TSNode) -> str:
    for child in node.children:
        if child.type == "visibility_modifier":
            return _node_text(child)
    return "public"


def _make_span(node: TSNode | None) -> Span | None:
    """Convert tree-sitter node positions to a Span (1-based)."""
    if node is None:  # pragma: no cover - callers guard against None
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
    except Exception:  # pragma: no cover - defensive
        return None
