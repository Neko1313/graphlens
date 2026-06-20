"""Tests for the Rust structural extractor."""

from graphlens import GraphLens, NodeKind

from graphlens_rust._visitor import (
    RustFileContext,
    RustStructureExtractor,
    _text,
    _walk_type,
    parse_rust,
)


def _extract(source: str) -> GraphLens:
    g = GraphLens()
    ctx = RustFileContext(
        project_name="p",
        module_qname="crate::m",
        file_id="f1",
        file_rel="src/m.rs",
    )
    RustStructureExtractor(g, ctx, lambda _p: "stdlib").extract(
        parse_rust(source.encode()).root_node
    )
    return g


def _names(g: GraphLens, kind: NodeKind) -> set[str]:
    return {n.name for n in g.nodes.values() if n.kind == kind}


def test_function():
    assert "foo" in _names(_extract("pub fn foo() {}\n"), NodeKind.FUNCTION)


def test_types_become_classes():
    g = _extract("struct S{} enum E{} trait T{} union U{ a: u8 }\n")
    assert {"S", "E", "T", "U"} <= _names(g, NodeKind.CLASS)


def test_type_alias_and_values():
    g = _extract("type A = u8; const C: u8 = 1; static X: u8 = 2;\n")
    assert "A" in _names(g, NodeKind.TYPE_ALIAS)
    assert {"C", "X"} <= _names(g, NodeKind.VARIABLE)


def test_impl_methods():
    g = _extract("struct S{} impl S { fn run(&self){} }\n")
    methods = [n for n in g.nodes.values() if n.kind == NodeKind.METHOD]
    assert any(
        n.name == "run" and "S.run" in n.qualified_name for n in methods
    )


def test_impl_generic_type():
    g = _extract("struct S<T>{x:T} impl<T> S<T> { fn run(&self){} }\n")
    methods = [n for n in g.nodes.values() if n.kind == NodeKind.METHOD]
    assert any("S.run" in n.qualified_name for n in methods)


def test_use_imports_create_external_symbols():
    g = _extract("use std::fmt;\n")
    assert "std::fmt" in _names(g, NodeKind.IMPORT)
    ext = [n for n in g.nodes.values() if n.kind == NodeKind.EXTERNAL_SYMBOL]
    assert any(n.metadata.get("origin") == "stdlib" for n in ext)


def test_duplicate_declaration_ignored():
    g = _extract("fn foo(){} fn foo(){}\n")
    foos = [
        n
        for n in g.nodes.values()
        if n.name == "foo" and n.kind == NodeKind.FUNCTION
    ]
    assert len(foos) == 1


def test_duplicate_import_ignored():
    g = _extract("use std::fmt; use std::fmt;\n")
    imps = [n for n in g.nodes.values() if n.kind == NodeKind.IMPORT]
    assert len(imps) == 1


def test_text_none_is_empty():
    assert _text(None) == ""


def test_walk_type_no_match():
    tree = parse_rust(b"fn x(){}\n")
    assert _walk_type(tree.root_node, "nonexistent_node_type") == []
