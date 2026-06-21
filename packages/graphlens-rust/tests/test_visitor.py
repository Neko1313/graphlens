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


def test_inline_module_items_extracted():
    # Items inside `mod foo { ... }` must not be dropped (idiomatic Rust).
    g = _extract("pub mod handlers { pub fn create() {} struct Repo {} }\n")
    assert "create" in _names(g, NodeKind.FUNCTION)
    assert "Repo" in _names(g, NodeKind.CLASS)


def test_inline_module_nested_qname():
    g = _extract("mod a { fn inner() {} }\n")
    inner = next(n for n in g.nodes.values() if n.name == "inner")
    assert inner.qualified_name == "crate::m::a::inner"


def test_bodyless_module_declaration_no_crash():
    # `mod util;` (external-file module) has no inline items to walk.
    assert _names(_extract("mod util;\n"), NodeKind.FUNCTION) == set()


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


# ---------------------------------------------------------------------------
# Call occurrence collection (TCK-12)
# ---------------------------------------------------------------------------


def _extractor(source: str):
    g = GraphLens()
    ctx = RustFileContext(
        project_name="p",
        module_qname="crate::m",
        file_id="f1",
        file_rel="src/m.rs",
    )
    ex = RustStructureExtractor(g, ctx, lambda _path: "stdlib")
    ex.extract(parse_rust(source.encode()).root_node)
    return g, ex


def test_collects_call_occurrences():
    src = (
        "fn f() {\n"
        "  foo();\n"
        "  util::helper();\n"
        "  obj.method();\n"
        "  Type::assoc();\n"
        "  outer(inner());\n"
        "}\n"
    )
    g, ex = _extractor(src)
    # foo, helper, method, assoc, outer, inner
    assert len(ex.occurrences) == 6
    assert all(o.role == "call" for o in ex.occurrences)
    fn = next(n for n in g.nodes.values() if n.kind == NodeKind.FUNCTION)
    assert {o.enclosing_id for o in ex.occurrences} == {fn.id}


def test_scoped_call_points_at_name():
    src = "fn f() {\n  util::helper();\n}\n"
    _g, ex = _extractor(src)
    occ = ex.occurrences[0]
    line = src.splitlines()[occ.line - 1]
    assert line[occ.col - 1 :].startswith("helper")


def test_field_call_points_at_field():
    src = "fn f() {\n  obj.method();\n}\n"
    _g, ex = _extractor(src)
    occ = ex.occurrences[0]
    line = src.splitlines()[occ.line - 1]
    assert line[occ.col - 1 :].startswith("method")


def test_call_in_impl_method_attributed_to_method():
    g, ex = _extractor("struct S{} impl S { fn run(&self){ helper(); } }\n")
    assert len(ex.occurrences) == 1
    method = next(n for n in g.nodes.values() if n.kind == NodeKind.METHOD)
    assert ex.occurrences[0].enclosing_id == method.id


def test_call_on_expression_result_is_skipped():
    _g, ex = _extractor("fn f() {\n  funcs[0]();\n}\n")
    assert ex.occurrences == []


def test_function_without_calls_has_no_occurrences():
    _g, ex = _extractor("fn f() { let x = 1; }\n")
    assert ex.occurrences == []


def test_shared_external_symbol_reused_across_files():
    g = GraphLens()
    for fid, rel in (("f1", "a.rs"), ("f2", "b.rs")):
        ctx = RustFileContext(
            project_name="p",
            module_qname="crate::m",
            file_id=fid,
            file_rel=rel,
        )
        RustStructureExtractor(g, ctx, lambda _path: "stdlib").extract(
            parse_rust(b"use std::fmt;\n").root_node
        )
    syms = [
        n for n in g.nodes.values() if n.kind == NodeKind.EXTERNAL_SYMBOL
    ]
    assert len(syms) == 1  # the "std::fmt" symbol is reused, not duplicated
