"""C# CST visitor using tree-sitter — builds graphlens nodes/relations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import tree_sitter_c_sharp as tscs
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

logger = logging.getLogger("graphlens_csharp")

_CSHARP_LANGUAGE = Language(tscs.language())
_parser = Parser(_CSHARP_LANGUAGE)

_ACCESS_MODIFIERS: frozenset[str] = frozenset(
    {"public", "private", "protected", "internal", "file"}
)

# Node types that can name an imported namespace/type in a ``using``.
_NAME_NODE_TYPES: tuple[str, ...] = (
    "identifier",
    "qualified_name",
    "generic_name",
)


def parse_csharp(source: bytes) -> Tree:
    """Parse C# source bytes and return a tree-sitter Tree."""
    return _parser.parse(source)


def extract_namespace(root: TSNode) -> str:
    """
    Return the file's primary namespace (``""`` for the global namespace).

    Takes the first ``file_scoped_namespace_declaration`` or
    ``namespace_declaration`` as the namespace the FILE node is attributed
    to. Type qualified names still come from the visitor's scope stack, so a
    file that (unusually) spans several namespaces stays correct even though
    only its first namespace owns the FILE.
    """
    for child in root.children:
        if child.type in (
            "file_scoped_namespace_declaration",
            "namespace_declaration",
        ):
            name_node = child.child_by_field_name("name")
            if name_node is not None:  # pragma: no cover - always present
                return _node_text(name_node)
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
      ``call``       — call-site of a method/constructor
      ``read``       — member/identifier read
      ``write``      — assignment target
      ``annotation`` — parameter / return / field / property type
      ``base``       — base class or implemented interface
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
    Classifies a ``using`` directive's origin from pre-computed name sets.

    Origin values (stored in ``Node.metadata["origin"]``):
    - ``"internal"``    — a namespace whose top matches a first-party assembly
    - ``"stdlib"``      — a BCL namespace (``System.*``)
    - ``"third_party"`` — a NuGet package's namespace
    - ``"unknown"``     — none of the above

    ``internal`` and ``stdlib`` match the namespace top segment
    case-sensitively; ``third_party`` matches the lowercased top segment
    (NuGet package-id segments are compared case-insensitively). ``stdlib`` is
    checked before ``third_party`` so ``System.*`` stays BCL even when a
    ``System.*`` NuGet package is also referenced.
    """

    stdlib: frozenset[str] = field(default_factory=frozenset)
    third_party: frozenset[str] = field(default_factory=frozenset)
    internal: frozenset[str] = field(default_factory=frozenset)

    def classify(self, top_level: str) -> str:
        """Return the origin of an import from its top namespace segment."""
        if top_level in self.internal:
            return "internal"
        if top_level in self.stdlib:
            return "stdlib"
        if top_level.lower() in self.third_party:
            return "third_party"
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


class CsharpASTVisitor:
    """
    Walks a tree-sitter C# CST and populates a GraphLens.

    Structural declarations (classes, interfaces, structs, records, enums,
    delegates, methods, constructors, properties, fields, events, parameters,
    imports) become nodes with ``DECLARES``/``IMPORTS``/``RESOLVES_TO`` edges.
    Use-sites (calls, type references, base types, member reads/writes) are
    collected as :class:`OccurrenceRef` for the post-visit resolution pass —
    this visitor never emits ``CALLS``/``REFERENCES``/``HAS_TYPE``/
    ``INHERITS_FROM``.
    """

    # C# method bodies can only nest a local function (not a type), so that
    # is the only member-like statement dispatched instead of scanned.
    _NESTED_DEF_TYPES = ("local_function_statement",)

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
        # the adapter as files are processed. Resolves internal imports to
        # their MODULE node by longest-prefix without scanning the graph.
        self._modules = modules if modules is not None else {}
        # Stack of qualified-name prefixes (current scope); "" = global ns.
        self._scope_stack: list[str] = [""]
        # Stack of node IDs for emitting DECLARES relations.
        self._container_stack: list[str] = [file_node_id]
        # Stack of NodeKind to know if we are inside a type body.
        self._kind_stack: list[NodeKind] = [NodeKind.FILE]
        # Occurrence use-sites collected during this visit.
        self.occurrences: list[OccurrenceRef] = []
        self.abs_file_path: str = str(ctx.file_path)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def visit(self, node: TSNode) -> None:
        """Dispatch a node to its ``_visit_<type>`` handler, else recurse."""
        handler = getattr(self, f"_visit_{node.type}", None)
        if handler:
            handler(node)
        else:
            self._visit_children(node)

    def _visit_children(self, node: TSNode) -> None:
        for child in node.children:
            self.visit(child)

    # ------------------------------------------------------------------
    # Namespaces
    # ------------------------------------------------------------------

    def _visit_file_scoped_namespace_declaration(self, node: TSNode) -> None:
        # File-scoped namespace applies to the rest of the file: push scope
        # and never pop — the following siblings are its members.
        name_node = node.child_by_field_name("name")
        if name_node is None:  # pragma: no cover - defensive
            return
        self._scope_stack.append(
            self._join(self._scope_stack[-1], _node_text(name_node))
        )

    def _visit_namespace_declaration(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:  # pragma: no cover - defensive
            return
        self._scope_stack.append(
            self._join(self._scope_stack[-1], _node_text(name_node))
        )
        body = node.child_by_field_name("body")
        if body is not None:  # pragma: no cover - always present
            self._visit_children(body)
        self._scope_stack.pop()

    # ------------------------------------------------------------------
    # Type declarations
    # ------------------------------------------------------------------

    def _visit_class_declaration(self, node: TSNode) -> None:
        self._handle_type(
            node,
            {
                "is_abstract": _has_modifier(node, "abstract"),
                "is_static": _has_modifier(node, "static"),
                "is_sealed": _has_modifier(node, "sealed"),
                "visibility": _visibility(node),
            },
        )

    def _visit_interface_declaration(self, node: TSNode) -> None:
        self._handle_type(
            node, {"is_interface": True, "visibility": _visibility(node)}
        )

    def _visit_struct_declaration(self, node: TSNode) -> None:
        self._handle_type(
            node, {"is_struct": True, "visibility": _visibility(node)}
        )

    def _visit_record_declaration(self, node: TSNode) -> None:
        self._handle_type(
            node,
            {
                "is_record": True,
                "is_abstract": _has_modifier(node, "abstract"),
                "visibility": _visibility(node),
            },
        )

    def _visit_enum_declaration(self, node: TSNode) -> None:
        self._handle_type(
            node, {"is_enum": True, "visibility": _visibility(node)}
        )

    def _handle_type(
        self, node: TSNode, metadata: dict[str, object]
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:  # pragma: no cover - defensive
            return
        name = _node_text(name_node)
        qname = self._qualify(name)

        type_node = self._make_node(
            NodeKind.CLASS, qname, name, node,
            metadata=metadata, name_node=name_node,
        )
        self._add_node_with_relation(type_node, RelationKind.DECLARES)

        base_list = _child_of_type(node, "base_list")
        if base_list is not None:
            for leaf in _base_type_heads(base_list):
                self._record_occurrence("base", leaf, type_node.id)

        self._push(qname, type_node.id, NodeKind.CLASS)
        # Positional record parameters double as constructor params. The
        # ``parameter_list`` is an unnamed child on a record (unlike a
        # method, where it is the ``parameters`` field).
        params = _child_of_type(node, "parameter_list")
        if params is not None:
            self._extract_parameters(params, type_node.id, qname)
        body = _type_body(node)
        if body is not None:
            self._visit_children(body)
        self._pop()

    def _visit_delegate_declaration(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:  # pragma: no cover - defensive
            return
        name = _node_text(name_node)
        delegate = self._make_node(
            NodeKind.CLASS, self._qualify(name), name, node,
            metadata={"is_delegate": True, "visibility": _visibility(node)},
            name_node=name_node,
        )
        self._add_node_with_relation(delegate, RelationKind.DECLARES)
        self._record_type(_return_type(node), delegate.id)
        params = node.child_by_field_name("parameters")
        if params is not None:  # pragma: no cover - always present
            for child in params.children:
                if child.type == "parameter":
                    self._record_type(
                        child.child_by_field_name("type"), delegate.id
                    )

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    def _visit_method_declaration(self, node: TSNode) -> None:
        self._handle_callable(
            node, node.child_by_field_name("name"), _return_type(node)
        )

    def _visit_constructor_declaration(self, node: TSNode) -> None:
        self._handle_callable(node, node.child_by_field_name("name"), None)

    def _visit_destructor_declaration(self, node: TSNode) -> None:
        self._handle_callable(node, node.child_by_field_name("name"), None)

    def _visit_operator_declaration(self, node: TSNode) -> None:
        name = node.child_by_field_name("name") or _child_of_type(
            node, "operator"
        )
        self._handle_callable(node, name, _return_type(node))

    def _visit_local_function_statement(self, node: TSNode) -> None:
        self._handle_callable(
            node, node.child_by_field_name("name"), _return_type(node)
        )

    def _handle_callable(
        self,
        node: TSNode,
        name_node: TSNode | None,
        returns_type: TSNode | None,
    ) -> None:
        if name_node is None:  # pragma: no cover - defensive
            return
        name = _node_text(name_node)
        qname = self._qualify(name)
        kind = (
            NodeKind.METHOD
            if self._kind_stack[-1] == NodeKind.CLASS
            else NodeKind.FUNCTION
        )

        func = self._make_node(
            kind, qname, name, node,
            metadata={
                "is_static": _has_modifier(node, "static"),
                "is_abstract": _has_modifier(node, "abstract"),
                "is_async": _has_modifier(node, "async"),
                "visibility": _visibility(node),
            },
            name_node=name_node,
        )
        self._add_node_with_relation(func, RelationKind.DECLARES)
        self._record_type(returns_type, func.id)

        self._push(qname, func.id, kind)
        params = node.child_by_field_name("parameters")
        if params is not None:  # pragma: no cover - always present
            self._extract_parameters(params, func.id, qname)
        body = node.child_by_field_name("body")
        if body is not None:
            self._walk_body(body, func.id)
        arrow = _child_of_type(node, "arrow_expression_clause")
        if arrow is not None:
            self._scan_value(arrow, func.id)
        self._pop()

    def _extract_parameters(
        self, params_node: TSNode, function_id: str, function_qname: str
    ) -> None:
        for child in params_node.children:
            if child.type != "parameter":
                continue
            name_node = child.child_by_field_name("name")
            if name_node is None:  # pragma: no cover - defensive
                continue
            param_name = _node_text(name_node)
            type_node = child.child_by_field_name("type")
            param_node = self._make_node(
                NodeKind.PARAMETER,
                f"{function_qname}.{param_name}",
                param_name,
                child,
                metadata={
                    "has_default": _child_of_type(child, "equals_value_clause")
                    is not None,
                },
                name_node=name_node,
            )
            self._safe_add_node(param_node)
            self._graph.add_relation(
                Relation(
                    source_id=function_id,
                    target_id=param_node.id,
                    kind=RelationKind.DECLARES,
                )
            )
            self._record_type(type_node, param_node.id)

    def _visit_property_declaration(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:  # pragma: no cover - defensive
            return
        name = _node_text(name_node)
        prop = self._make_node(
            NodeKind.ATTRIBUTE, self._qualify(name), name, node,
            metadata={"is_property": True, "visibility": _visibility(node)},
            name_node=name_node,
        )
        self._add_node_with_relation(prop, RelationKind.DECLARES)
        self._record_type(node.child_by_field_name("type"), prop.id)
        accessors = node.child_by_field_name("accessors")
        if accessors is not None:
            for accessor in accessors.children:
                if accessor.type != "accessor_declaration":
                    continue
                body = accessor.child_by_field_name("body")
                if body is not None:
                    self._walk_body(body, prop.id)
                arrow = _child_of_type(accessor, "arrow_expression_clause")
                if arrow is not None:
                    self._scan_value(arrow, prop.id)
        arrow = _child_of_type(node, "arrow_expression_clause")
        if arrow is not None:
            self._scan_value(arrow, prop.id)

    def _visit_field_declaration(self, node: TSNode) -> None:
        self._handle_variable_declaration(
            node, {"visibility": _visibility(node)}
        )

    def _visit_event_field_declaration(self, node: TSNode) -> None:
        self._handle_variable_declaration(
            node, {"is_event": True, "visibility": _visibility(node)}
        )

    def _handle_variable_declaration(
        self, node: TSNode, metadata: dict[str, object]
    ) -> None:
        var_decl = _child_of_type(node, "variable_declaration")
        if var_decl is None:  # pragma: no cover - defensive
            return
        type_node = var_decl.child_by_field_name("type")
        # Fields and events are always type members in C#, so they are
        # ATTRIBUTE nodes (there is no namespace-level field to be a VARIABLE).
        for declarator in var_decl.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            if name_node is None:  # pragma: no cover - defensive
                continue
            name = _node_text(name_node)
            member = self._make_node(
                NodeKind.ATTRIBUTE, self._qualify(name), name, declarator,
                metadata=metadata, name_node=name_node,
            )
            self._add_node_with_relation(member, RelationKind.DECLARES)
            self._record_type(type_node, member.id)
            # A field declarator is flat: name, '=', initializer expression.
            # Scan everything past the name so an initializer's calls/reads
            # are attributed to the field.
            for index, child in enumerate(declarator.children):
                if index == 0 or child.type == "=":
                    continue
                self._scan_value(child, member.id)

    def _visit_enum_member_declaration(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:  # pragma: no cover - defensive
            return
        name = _node_text(name_node)
        member = self._make_node(
            NodeKind.ATTRIBUTE, self._qualify(name), name, node,
            metadata={"is_enum_member": True}, name_node=name_node,
        )
        self._add_node_with_relation(member, RelationKind.DECLARES)

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _visit_using_directive(self, node: TSNode) -> None:
        eq = _child_of_type(node, "=")
        children = list(node.children)
        alias: str | None = None
        target: TSNode | None = None
        if eq is not None:
            idx = children.index(eq)
            alias = _node_text(children[idx - 1])
            target = children[idx + 1]
        else:
            target = next(
                (c for c in children if c.type in _NAME_NODE_TYPES), None
            )
        if target is None:  # pragma: no cover - defensive
            return
        ext_qname = _node_text(target)
        local = alias if alias is not None else ext_qname.rsplit(".", 1)[-1]
        self._emit_import(local_name=local, ext_qname=ext_qname, alias=alias)

    def _emit_import(
        self, *, local_name: str, ext_qname: str, alias: str | None
    ) -> None:
        top = ext_qname.split(".", maxsplit=1)[0]
        origin = self._classifier.classify(top)

        import_node = self._make_node(
            NodeKind.IMPORT, self._qualify(local_name), local_name,
            metadata={
                "alias": alias,
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

    # ------------------------------------------------------------------
    # Top-level statements (C# 9+ top-level programs)
    # ------------------------------------------------------------------

    def _visit_global_statement(self, node: TSNode) -> None:
        for child in node.children:
            self._scan_value(child, self._file_node_id)

    # ------------------------------------------------------------------
    # Value scanning (calls / reads / writes)
    # ------------------------------------------------------------------

    def _walk_body(self, body: TSNode, enclosing_id: str) -> None:
        """Walk a method/accessor body, recording use-sites once each."""
        for child in body.children:
            if child.type in self._NESTED_DEF_TYPES:
                self.visit(child)
            else:
                self._scan_value(child, enclosing_id)

    def _scan_value(
        self, node: TSNode, enclosing_id: str
    ) -> None:
        """Record ``call``/``read``/``write`` occurrences in an expression."""
        t = node.type
        if t == "invocation_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:  # pragma: no cover - always present
                leaf = _call_target_leaf(fn)
                if leaf is not None:
                    self._record_occurrence("call", leaf, enclosing_id)
                self._scan_receiver(fn, enclosing_id)
            self._scan_arguments(node, enclosing_id)
            return
        if t == "object_creation_expression":
            type_node = node.child_by_field_name("type")
            if type_node is not None:  # pragma: no cover - always present
                leaf = _type_head(type_node)
                if leaf is not None:
                    self._record_occurrence("call", leaf, enclosing_id)
            self._scan_arguments(node, enclosing_id)
            return
        if t == "member_access_expression":
            self._record_occurrence(
                "read", node.child_by_field_name("name"), enclosing_id
            )
            expr = node.child_by_field_name("expression")
            if expr is not None:  # pragma: no cover - always present
                self._scan_value(expr, enclosing_id)
            return
        if t == "assignment_expression":
            self._scan_assignment(node, enclosing_id)
            return
        if t == "identifier":
            self._record_occurrence("read", node, enclosing_id)
            return
        for child in node.children:
            self._scan_value(child, enclosing_id)

    def _scan_receiver(self, fn: TSNode, enclosing_id: str) -> None:
        if fn.type == "member_access_expression":
            expr = fn.child_by_field_name("expression")
            if expr is not None:  # pragma: no cover - always present
                self._scan_value(expr, enclosing_id)
        elif fn.type not in ("identifier", "generic_name", "qualified_name"):
            self._scan_value(fn, enclosing_id)

    def _scan_assignment(self, node: TSNode, enclosing_id: str) -> None:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is not None and left.type == "member_access_expression":
            self._record_occurrence(
                "write", left.child_by_field_name("name"), enclosing_id
            )
            expr = left.child_by_field_name("expression")
            if expr is not None:  # pragma: no cover - always present
                self._scan_value(expr, enclosing_id)
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

    def _record_type(
        self, type_node: TSNode | None, enclosing_id: str
    ) -> None:
        """
        Record a type annotation occurrence, if a type node is present.

        Accepts ``None`` (e.g. a constructor has no return type) so callers
        can pass ``child_by_field_name(...)`` straight through without a guard.
        """
        if type_node is None:
            return
        for leaf in _collect_type_refs(type_node):
            self._record_occurrence("annotation", leaf, enclosing_id)

    def _record_occurrence(
        self, role: str, name_node: TSNode | None, enclosing_id: str
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

    # ------------------------------------------------------------------
    # Node helpers
    # ------------------------------------------------------------------

    def _qualify(self, name: str) -> str:
        return self._join(self._scope_stack[-1], name)

    @staticmethod
    def _join(prefix: str, name: str) -> str:
        return f"{prefix}.{name}" if prefix else name

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
        """
        Return the MODULE id for ``qname`` or its longest namespace prefix.

        ``Acme.Billing.Invoice`` resolves to the ``Acme.Billing`` namespace
        MODULE even when the ``Invoice`` type is not yet its own node. Uses
        the shared ``modules`` index (O(depth)) rather than scanning the graph.
        """
        parts = qname.split(".")
        for length in range(len(parts), 0, -1):
            candidate = ".".join(parts[:length])
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


def _child_of_type(node: TSNode, type_name: str) -> TSNode | None:
    return next((c for c in node.children if c.type == type_name), None)


def _type_body(node: TSNode) -> TSNode | None:
    """
    Return a type's member body, or None for a bodyless (record) type.

    Every C# type node — class, interface, struct, record-with-body and enum
    (whose members hang off an ``enum_member_declaration_list``) — exposes its
    members through the ``body`` field. A positional record with no block
    (``record R(int X);``) has no ``body``, hence the ``None``.
    """
    return node.child_by_field_name("body")


def _return_type(node: TSNode) -> TSNode | None:
    """
    Return a callable's return-type node.

    Methods expose it as the ``returns`` field; local functions, operators and
    delegates use ``type``. Trying both keeps one code path for all callables.
    """
    return node.child_by_field_name("returns") or node.child_by_field_name(
        "type"
    )


def _type_head(node: TSNode) -> TSNode | None:
    """Return the head identifier of a (possibly generic/qualified) type."""
    if node.type == "identifier":
        return node
    if node.type == "qualified_name":
        return node.child_by_field_name("name")
    if node.type == "generic_name":
        return _child_of_type(node, "identifier")
    return None


def _base_type_heads(base_list: TSNode) -> list[TSNode]:
    """
    Return the head identifier of each base type in a ``base_list``.

    Only the base type itself is returned, not its generic arguments
    (``IRepo<Invoice>`` yields ``IRepo``), so the resulting ``base`` edges
    point at real base types rather than type arguments.
    """
    heads: list[TSNode] = []
    for child in base_list.children:
        if child.type in ("identifier", "qualified_name", "generic_name"):
            head = _type_head(child)
            if head is not None:  # pragma: no cover - always present
                heads.append(head)
    return heads


def _call_target_leaf(fn: TSNode) -> TSNode | None:
    """Return the name leaf identifying the callee of an invocation."""
    if fn.type == "member_access_expression":
        return fn.child_by_field_name("name")
    return _type_head(fn)


def _collect_type_refs(node: TSNode) -> list[TSNode]:
    """
    Collect the identifier leaves a type expression references.

    Generic arguments are included (``List<Invoice>`` → ``List`` and
    ``Invoice``); built-in ``predefined_type`` names (``int``, ``string``,
    ``void``) are skipped.
    """
    out: list[TSNode] = []
    _collect_type_refs_into(node, out)
    return out


def _collect_type_refs_into(node: TSNode, out: list[TSNode]) -> None:
    t = node.type
    if t == "predefined_type":
        return
    if t == "identifier":
        out.append(node)
        return
    if t == "qualified_name":
        name = node.child_by_field_name("name")
        if name is not None:  # pragma: no cover - always present
            out.append(name)
        return
    if t == "generic_name":
        head = _child_of_type(node, "identifier")
        if head is not None:  # pragma: no cover - always present
            out.append(head)
        type_args = _child_of_type(node, "type_argument_list")
        if type_args is not None:  # pragma: no cover - always present
            for child in type_args.children:
                _collect_type_refs_into(child, out)
        return
    for child in node.children:
        _collect_type_refs_into(child, out)


def _has_modifier(node: TSNode, modifier: str) -> bool:
    return any(
        c.type == "modifier" and _node_text(c) == modifier
        for c in node.children
    )


def _visibility(node: TSNode) -> str:
    for child in node.children:
        if child.type == "modifier" and _node_text(child) in _ACCESS_MODIFIERS:
            return _node_text(child)
    return ""


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
