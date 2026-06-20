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
    from collections.abc import Callable

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
    ) -> None:
        node_id = make_node_id(self._ctx.project_name, qname, kind.value)
        if node_id in self._graph.nodes:
            return
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

    def _on_function_declaration(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return  # pragma: no cover
        qname = f"{self._ctx.package_qname}.{_text(name_node)}"
        self._declare(
            qname, _text(name_node), NodeKind.FUNCTION, node, name_node
        )

    def _on_method_declaration(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return  # pragma: no cover
        recv = self._receiver_type(node)
        prefix = f"{recv}." if recv else ""
        qname = f"{self._ctx.package_qname}.{prefix}{_text(name_node)}"
        self._declare(
            qname, _text(name_node), NodeKind.METHOD, node, name_node
        )

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
            self._declare(qname, _text(name_node), kind, spec, name_node)

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
