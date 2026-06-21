"""Tree-sitter Go structural extraction (packages, types, funcs, imports)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import tree_sitter_go as ts_go
from graphlens import Node, NodeKind, Relation, RelationKind
from graphlens.utils import make_node_id
from graphlens.utils.span import Span
from tree_sitter import Language, Parser

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from graphlens import GraphLens
    from tree_sitter import Node as TSNode
    from tree_sitter import Tree

_LANGUAGE = Language(ts_go.language())
_parser = Parser(_LANGUAGE)


def parse_go(source: bytes) -> Tree:
    """Parse Go source bytes into a tree-sitter tree."""
    return _parser.parse(source)


def _span(node: TSNode) -> Span:
    return Span(
        start_line=node.start_point[0] + 1,
        start_col=node.start_point[1] + 1,
        end_line=node.end_point[0] + 1,
        end_col=node.end_point[1] + 1,
    )


def _text(node: TSNode | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _walk_type(node: TSNode, type_name: str) -> list[TSNode]:
    """Return all descendants of ``node`` with the given node type."""
    out: list[TSNode] = []
    stack = list(node.children)
    while stack:
        current = stack.pop()
        if current.type == type_name:
            out.append(current)
        else:
            stack.extend(current.children)
    return out


def _descendants(node: TSNode) -> Iterator[TSNode]:
    """Yield ``node`` and every descendant (full recursion, no pruning)."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def _called_name(fn: TSNode) -> TSNode | None:
    """Return the identifier/field token naming a call's callee, or None."""
    if fn.type == "identifier":
        return fn  # foo()
    if fn.type == "selector_expression":
        return fn.child_by_field_name("field")  # pkg.Foo() / recv.Method()
    return None  # calling an expression result, e.g. funcs[0]()


def _type_name_node(type_node: TSNode | None) -> TSNode | None:
    """Return the type_identifier naming a (possibly qualified) type."""
    if type_node is None:
        return None
    if type_node.type == "type_identifier":
        return type_node  # Animal
    if type_node.type == "qualified_type":
        return type_node.child_by_field_name("name")  # pkg.Base -> Base
    return None  # e.g. a generic instantiation Base[int]


@dataclass(frozen=True)
class OccurrenceRef:
    """
    A use-site for the resolution pass to bind to a definition.

    Coordinates are 1-based (matching :class:`Span`). The only role emitted
    so far is ``call`` (CALLS); type/embedding roles are staged separately.
    """

    role: str
    line: int
    col: int
    enclosing_id: str
    span: Span


@dataclass
class GoFileContext:
    """Per-file context for structural extraction."""

    project_name: str
    package_qname: str
    file_id: str
    file_rel: str


class GoStructureExtractor:
    """Walk a Go file's top-level declarations and populate the graph."""

    def __init__(
        self,
        graph: GraphLens,
        ctx: GoFileContext,
        classify: Callable[[str], str],
    ) -> None:
        """Bind the extractor to a graph, file context, and classifier."""
        self._graph = graph
        self._ctx = ctx
        self._classify = classify
        self.occurrences: list[OccurrenceRef] = []

    def extract(self, root: TSNode) -> None:
        """Dispatch each top-level child to its ``_on_<type>`` handler."""
        for child in root.children:
            handler = getattr(self, f"_on_{child.type}", None)
            if handler is not None:
                handler(child)

    def _declare(
        self,
        qname: str,
        name: str,
        kind: NodeKind,
        full_node: TSNode,
        name_node: TSNode,
    ) -> str:
        node_id = make_node_id(self._ctx.project_name, qname, kind.value)
        if node_id in self._graph.nodes:
            return node_id
        self._graph.add_node(
            Node(
                id=node_id,
                kind=kind,
                qualified_name=qname,
                name=name,
                file_path=self._ctx.file_rel,
                span=_span(full_node),
                metadata={"name_span": _span(name_node)},
            )
        )
        self._graph.add_relation(
            Relation(self._ctx.file_id, node_id, RelationKind.DECLARES)
        )
        return node_id

    def _add_occurrence(
        self, role: str, name_node: TSNode, enclosing_id: str
    ) -> None:
        self.occurrences.append(
            OccurrenceRef(
                role=role,
                line=name_node.start_point[0] + 1,
                col=name_node.start_point[1] + 1,
                enclosing_id=enclosing_id,
                span=_span(name_node),
            )
        )

    def _collect_calls(self, scope: TSNode, enclosing_id: str) -> None:
        """Record a ``call`` occurrence for every call under ``scope``."""
        for node in _descendants(scope):
            if node.type != "call_expression":
                continue
            fn = node.child_by_field_name("function")
            if fn is None:  # pragma: no cover - grammar guarantees a function
                continue
            name_node = _called_name(fn)
            if name_node is not None:
                self._add_occurrence("call", name_node, enclosing_id)

    def _collect_bases(self, type_node: TSNode, enclosing_id: str) -> None:
        """Record a ``base`` occurrence for each embedded type."""
        if type_node.type == "struct_type":
            self._collect_struct_bases(type_node, enclosing_id)
        else:  # interface_type
            self._collect_iface_bases(type_node, enclosing_id)

    def _collect_struct_bases(
        self, struct_type: TSNode, enclosing_id: str
    ) -> None:
        for fdl in struct_type.children:
            if fdl.type != "field_declaration_list":
                continue
            for fd in fdl.children:
                if fd.type != "field_declaration":
                    continue
                if fd.child_by_field_name("name") is not None:
                    continue  # a named field, not an embedded type
                name_node = _type_name_node(fd.child_by_field_name("type"))
                if name_node is not None:
                    self._add_occurrence("base", name_node, enclosing_id)

    def _collect_iface_bases(
        self, iface_type: TSNode, enclosing_id: str
    ) -> None:
        for elem in iface_type.children:
            if elem.type != "type_elem":
                continue
            for child in elem.named_children:
                name_node = _type_name_node(child)
                if name_node is not None:
                    self._add_occurrence("base", name_node, enclosing_id)

    def _on_function_declaration(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return  # pragma: no cover
        qname = f"{self._ctx.package_qname}.{_text(name_node)}"
        node_id = self._declare(
            qname, _text(name_node), NodeKind.FUNCTION, node, name_node
        )
        self._collect_calls(node, node_id)

    def _on_method_declaration(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return  # pragma: no cover
        recv = self._receiver_type(node)
        prefix = f"{recv}." if recv else ""
        qname = f"{self._ctx.package_qname}.{prefix}{_text(name_node)}"
        node_id = self._declare(
            qname, _text(name_node), NodeKind.METHOD, node, name_node
        )
        self._collect_calls(node, node_id)

    def _on_type_declaration(self, node: TSNode) -> None:
        for spec in node.children:
            if spec.type not in ("type_spec", "type_alias"):
                continue
            name_node = spec.child_by_field_name("name")
            if name_node is None:
                continue  # pragma: no cover
            type_node = spec.child_by_field_name("type")
            is_struct_like = (
                spec.type == "type_spec"
                and type_node is not None
                and type_node.type in ("struct_type", "interface_type")
            )
            kind = NodeKind.CLASS if is_struct_like else NodeKind.TYPE_ALIAS
            qname = f"{self._ctx.package_qname}.{_text(name_node)}"
            node_id = self._declare(
                qname, _text(name_node), kind, spec, name_node
            )
            if is_struct_like and type_node is not None:
                self._collect_bases(type_node, node_id)

    def _on_var_declaration(self, node: TSNode) -> None:
        self._handle_value_specs(node, "var_spec")

    def _on_const_declaration(self, node: TSNode) -> None:
        self._handle_value_specs(node, "const_spec")

    def _handle_value_specs(self, node: TSNode, spec_type: str) -> None:
        for spec in _walk_type(node, spec_type):
            for name_node in spec.children_by_field_name("name"):
                qname = f"{self._ctx.package_qname}.{_text(name_node)}"
                self._declare(
                    qname,
                    _text(name_node),
                    NodeKind.VARIABLE,
                    spec,
                    name_node,
                )

    def _on_import_declaration(self, node: TSNode) -> None:
        for spec in _walk_type(node, "import_spec"):
            path_node = spec.child_by_field_name("path")
            import_path = _text(path_node).strip('"')
            if not import_path:
                continue
            origin = self._classify(import_path)
            self._declare_import(spec, import_path, origin)

    def _declare_import(
        self, spec: TSNode, import_path: str, origin: str
    ) -> None:
        imp_qname = f"{self._ctx.file_rel}::{import_path}"
        imp_id = make_node_id(
            self._ctx.project_name, imp_qname, NodeKind.IMPORT.value
        )
        if imp_id in self._graph.nodes:
            return
        self._graph.add_node(
            Node(
                id=imp_id,
                kind=NodeKind.IMPORT,
                qualified_name=imp_qname,
                name=import_path,
                file_path=self._ctx.file_rel,
                span=_span(spec),
                metadata={"origin": origin, "import_path": import_path},
            )
        )
        self._graph.add_relation(
            Relation(self._ctx.file_id, imp_id, RelationKind.DECLARES)
        )
        sym_id = make_node_id(
            self._ctx.project_name,
            import_path,
            NodeKind.EXTERNAL_SYMBOL.value,
        )
        if sym_id not in self._graph.nodes:
            self._graph.add_node(
                Node(
                    id=sym_id,
                    kind=NodeKind.EXTERNAL_SYMBOL,
                    qualified_name=import_path,
                    name=import_path.rsplit("/", maxsplit=1)[-1],
                    metadata={"origin": origin},
                )
            )
        self._graph.add_relation(
            Relation(imp_id, sym_id, RelationKind.RESOLVES_TO)
        )

    def _receiver_type(self, method_node: TSNode) -> str:
        recv = method_node.child_by_field_name("receiver")
        if recv is None:
            return ""  # pragma: no cover
        for ident in _walk_type(recv, "type_identifier"):
            return _text(ident)
        return ""  # pragma: no cover
