"""Tests for the Go structural extractor."""

from graphlens import GraphLens, NodeKind

from graphlens_go._visitor import (
    GoFileContext,
    GoStructureExtractor,
    _text,
    _walk_type,
    parse_go,
)


def _extract(source: str) -> GraphLens:
    g = GraphLens()
    ctx = GoFileContext(
        project_name="p",
        package_qname="m/pkg",
        file_id="file1",
        file_rel="pkg/a.go",
    )
    GoStructureExtractor(g, ctx, lambda _path: "stdlib").extract(
        parse_go(source.encode()).root_node
    )
    return g


def _names(g: GraphLens, kind: NodeKind) -> set[str]:
    return {n.name for n in g.nodes.values() if n.kind == kind}


def test_function_and_method():
    g = _extract("package pkg\nfunc Foo() {}\nfunc (r Bar) Baz() {}\n")
    assert "Foo" in _names(g, NodeKind.FUNCTION)
    assert "Baz" in _names(g, NodeKind.METHOD)
    methods = [n for n in g.nodes.values() if n.kind == NodeKind.METHOD]
    assert any("Bar.Baz" in n.qualified_name for n in methods)


def test_types_struct_interface_alias():
    g = _extract(
        "package pkg\ntype S struct{}\ntype I interface{}\ntype A = S\n"
    )
    classes = _names(g, NodeKind.CLASS)
    assert "S" in classes
    assert "I" in classes
    assert "A" in _names(g, NodeKind.TYPE_ALIAS)


def test_var_and_const():
    g = _extract("package pkg\nvar X int\nconst Y = 1\n")
    variables = _names(g, NodeKind.VARIABLE)
    assert "X" in variables
    assert "Y" in variables


def test_imports_create_external_symbols():
    g = _extract('package pkg\nimport (\n\t"fmt"\n)\n')
    assert "fmt" in _names(g, NodeKind.IMPORT)
    ext = [n for n in g.nodes.values() if n.kind == NodeKind.EXTERNAL_SYMBOL]
    assert any(n.metadata.get("origin") == "stdlib" for n in ext)


def test_single_import_spec():
    g = _extract('package pkg\nimport "os"\n')
    assert "os" in _names(g, NodeKind.IMPORT)


def test_name_span_recorded():
    g = _extract("package pkg\nfunc Foo() {}\n")
    foo = next(n for n in g.nodes.values() if n.name == "Foo")
    assert "name_span" in foo.metadata


def test_text_none_is_empty():
    assert _text(None) == ""


def test_walk_type_no_match():
    tree = parse_go(b"package pkg\n")
    assert _walk_type(tree.root_node, "nonexistent_node_type") == []


def test_duplicate_declaration_ignored():
    g = _extract("package pkg\nfunc Foo() {}\nfunc Foo() {}\n")
    foos = [
        n
        for n in g.nodes.values()
        if n.name == "Foo" and n.kind == NodeKind.FUNCTION
    ]
    assert len(foos) == 1


def test_empty_import_path_skipped():
    g = _extract('package pkg\nimport ""\n')
    assert not [n for n in g.nodes.values() if n.kind == NodeKind.IMPORT]


def test_duplicate_import_ignored():
    g = _extract('package pkg\nimport (\n\t"fmt"\n\t"fmt"\n)\n')
    imps = [n for n in g.nodes.values() if n.kind == NodeKind.IMPORT]
    assert len(imps) == 1
