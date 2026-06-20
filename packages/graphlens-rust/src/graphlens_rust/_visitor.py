"""Tree-sitter Rust structural extraction (modules, types, fns, imports)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import tree_sitter_rust as ts_rust
from graphlens import Node, NodeKind, Relation, RelationKind
from graphlens.utils import make_node_id
from graphlens.utils.span import Span
from tree_sitter import Language, Parser

if TYPE_CHECKING:
    from collections.abc import Callable

    from graphlens import GraphLens
    from tree_sitter import Node as TSNode
    from tree_sitter import Tree

_LANGUAGE = Language(ts_rust.language())
_parser = Parser(_LANGUAGE)


def parse_rust(source: bytes) -> Tree:
    """Parse Rust source bytes into a tree-sitter tree."""
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
class RustFileContext:
    """Per-file context for structural extraction."""

    project_name: str
    module_qname: str
    file_id: str
    file_rel: str


class RustStructureExtractor:
    """Walk a Rust file's top-level items and populate the graph."""

    def __init__(
        self,
        graph: GraphLens,
        ctx: RustFileContext,
        classify: Callable[[str], str],
    ) -> None:
        """Bind the extractor to a graph, file context, and classifier."""
        self._graph = graph
        self._ctx = ctx
        self._classify = classify

    def extract(self, root: TSNode) -> None:
        """Dispatch each top-level item to its ``_on_<type>`` handler."""
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

    def _declare_named(self, node: TSNode, kind: NodeKind) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return  # pragma: no cover
        qname = f"{self._ctx.module_qname}::{_text(name_node)}"
        self._declare(qname, _text(name_node), kind, node, name_node)

    def _on_function_item(self, node: TSNode) -> None:
        self._declare_named(node, NodeKind.FUNCTION)

    def _on_struct_item(self, node: TSNode) -> None:
        self._declare_named(node, NodeKind.CLASS)

    def _on_enum_item(self, node: TSNode) -> None:
        self._declare_named(node, NodeKind.CLASS)

    def _on_trait_item(self, node: TSNode) -> None:
        self._declare_named(node, NodeKind.CLASS)

    def _on_union_item(self, node: TSNode) -> None:
        self._declare_named(node, NodeKind.CLASS)

    def _on_type_item(self, node: TSNode) -> None:
        self._declare_named(node, NodeKind.TYPE_ALIAS)

    def _on_const_item(self, node: TSNode) -> None:
        self._declare_named(node, NodeKind.VARIABLE)

    def _on_static_item(self, node: TSNode) -> None:
        self._declare_named(node, NodeKind.VARIABLE)

    def _on_impl_item(self, node: TSNode) -> None:
        type_name = self._impl_type_name(node)
        body = node.child_by_field_name("body")
        if body is None:
            return  # pragma: no cover
        for item in body.children:
            if item.type != "function_item":
                continue
            name_node = item.child_by_field_name("name")
            if name_node is None:
                continue  # pragma: no cover
            prefix = f"{type_name}." if type_name else ""
            qname = (
                f"{self._ctx.module_qname}::{prefix}{_text(name_node)}"
            )
            self._declare(
                qname, _text(name_node), NodeKind.METHOD, item, name_node
            )

    def _on_use_declaration(self, node: TSNode) -> None:
        arg = node.child_by_field_name("argument")
        import_path = _text(arg)
        if not import_path:
            return  # pragma: no cover
        origin = self._classify(import_path)
        self._declare_import(node, import_path, origin)

    def _declare_import(
        self, node: TSNode, import_path: str, origin: str
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
                span=_span(node),
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
                    name=import_path.rsplit("::", maxsplit=1)[-1],
                    metadata={"origin": origin},
                )
            )
        self._graph.add_relation(
            Relation(imp_id, sym_id, RelationKind.RESOLVES_TO)
        )

    def _impl_type_name(self, impl_node: TSNode) -> str:
        type_node = impl_node.child_by_field_name("type")
        if type_node is None:
            return ""  # pragma: no cover
        idents = _walk_type(type_node, "type_identifier")
        if not idents:
            return _text(type_node)
        return _text(min(idents, key=lambda n: n.start_byte))
