"""Tests for the Go structural extractor."""

from graphlens import GraphLens, NodeKind

from graphlens_go._visitor import (
    GoFileContext,
    GoStructureExtractor,
    _text,
    _type_name_node,
    _walk_type,
    parse_go,
)


def test_type_name_node_none():
    assert _type_name_node(None) is None


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


# ---------------------------------------------------------------------------
# Call occurrence collection (TCK-12)
# ---------------------------------------------------------------------------


def _extractor(source: str):
    g = GraphLens()
    ctx = GoFileContext(
        project_name="p",
        package_qname="m/pkg",
        file_id="file1",
        file_rel="pkg/a.go",
    )
    ex = GoStructureExtractor(g, ctx, lambda _path: "stdlib")
    ex.extract(parse_go(source.encode()).root_node)
    return g, ex


def test_collects_call_occurrences():
    src = (
        "package pkg\n"
        "func F() {\n"
        "  foo()\n"
        "  pkg.Bar()\n"
        "  r.Baz()\n"
        "  outer(inner())\n"
        "}\n"
    )
    g, ex = _extractor(src)
    assert len(ex.occurrences) == 5  # foo, Bar, Baz, outer, inner
    assert all(o.role == "call" for o in ex.occurrences)
    func = next(n for n in g.nodes.values() if n.kind == NodeKind.FUNCTION)
    assert {o.enclosing_id for o in ex.occurrences} == {func.id}


def test_selector_call_points_at_field():
    src = "package pkg\nfunc F() {\n  pkg.Bar()\n}\n"
    _g, ex = _extractor(src)
    occ = ex.occurrences[0]
    line = src.splitlines()[occ.line - 1]
    assert line[occ.col - 1 :].startswith("Bar")


def test_call_in_method_attributed_to_method():
    g, ex = _extractor("package pkg\nfunc (r T) M() {\n  helper()\n}\n")
    assert len(ex.occurrences) == 1
    method = next(n for n in g.nodes.values() if n.kind == NodeKind.METHOD)
    assert ex.occurrences[0].enclosing_id == method.id


def test_call_on_expression_result_is_skipped():
    _g, ex = _extractor("package pkg\nfunc F() {\n  funcs[0]()\n}\n")
    assert ex.occurrences == []


def test_function_without_calls_has_no_occurrences():
    _g, ex = _extractor("package pkg\nfunc F() { x := 1; _ = x }\n")
    assert ex.occurrences == []


def _bases(ex):
    return [o for o in ex.occurrences if o.role == "base"]


def test_struct_embedding_collected_as_base():
    src = (
        "package pkg\n"
        "type Dog struct {\n"
        "  Animal\n"
        "  pkg.Base\n"
        "  *Cat\n"
        "  Name string\n"
        "}\n"
    )
    g, ex = _extractor(src)
    bases = _bases(ex)
    assert len(bases) == 3  # Animal, Base, Cat — not the named field Name
    cls = next(n for n in g.nodes.values() if n.kind == NodeKind.CLASS)
    assert {o.enclosing_id for o in bases} == {cls.id}


def test_struct_generic_embedding_skipped():
    _g, ex = _extractor(
        "package pkg\ntype X struct {\n  Base[int]\n}\n"
    )
    assert _bases(ex) == []


def test_interface_embedding_collected_as_base():
    src = (
        "package pkg\n"
        "type RW interface {\n"
        "  Reader\n"
        "  io.Closer\n"
        "  Read() int\n"
        "}\n"
    )
    _g, ex = _extractor(src)
    assert len(_bases(ex)) == 2  # Reader, Closer — not the method Read


def test_struct_base_points_at_type_name():
    src = "package pkg\ntype Dog struct {\n  Animal\n}\n"
    _g, ex = _extractor(src)
    occ = _bases(ex)[0]
    line = src.splitlines()[occ.line - 1]
    assert line[occ.col - 1 :].startswith("Animal")


def test_interface_type_constraint_skipped():
    _g, ex = _extractor("package pkg\ntype C interface {\n  ~int\n}\n")
    assert _bases(ex) == []


def test_shared_external_symbol_reused_across_files():
    g = GraphLens()
    for fid, rel in (("f1", "a.go"), ("f2", "b.go")):
        ctx = GoFileContext(
            project_name="p",
            package_qname="m",
            file_id=fid,
            file_rel=rel,
        )
        GoStructureExtractor(g, ctx, lambda _path: "stdlib").extract(
            parse_go(b'package m\nimport "fmt"\n').root_node
        )
    syms = [
        n for n in g.nodes.values() if n.kind == NodeKind.EXTERNAL_SYMBOL
    ]
    assert len(syms) == 1  # the "fmt" symbol is reused, not duplicated
