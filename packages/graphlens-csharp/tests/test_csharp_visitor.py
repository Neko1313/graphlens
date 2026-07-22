from pathlib import Path

from graphlens import GraphLens, Node, NodeKind, RelationKind
from graphlens.utils import make_node_id

from graphlens_csharp._visitor import (
    CsharpASTVisitor,
    ImportClassifier,
    VisitorContext,
    extract_namespace,
    parse_csharp,
)


def _visit(source, *, classifier=None, modules=None, project="P", file="T.cs"):
    graph = GraphLens()
    ctx = VisitorContext(
        project_name=project, file_path=Path(file), namespace=""
    )
    file_id = make_node_id(project, file, NodeKind.FILE.value)
    graph.add_node(
        Node(id=file_id, kind=NodeKind.FILE, qualified_name=file, name=file)
    )
    tree = parse_csharp(source.encode())
    visitor = CsharpASTVisitor(
        ctx, graph, file_id, source.encode(), classifier, modules
    )
    visitor.visit(tree.root_node)
    return graph, visitor, file_id


def _kind(graph, kind):
    return [n for n in graph.nodes.values() if n.kind == kind]


def _qnames(graph, kind):
    return {n.qualified_name for n in _kind(graph, kind)}


def _roles(visitor):
    return [o.role for o in visitor.occurrences]


# ---------------------------------------------------------------------------
# parse / namespace / classifier
# ---------------------------------------------------------------------------


def test_parse_csharp():
    assert parse_csharp(b"class C {}").root_node.type == "compilation_unit"


def test_extract_namespace_file_scoped():
    tree = parse_csharp(b"namespace A.B; class C {}")
    assert extract_namespace(tree.root_node) == "A.B"


def test_extract_namespace_block():
    tree = parse_csharp(b"namespace A.B { class C {} }")
    assert extract_namespace(tree.root_node) == "A.B"


def test_extract_namespace_global():
    assert extract_namespace(parse_csharp(b"class C {}").root_node) == ""


def test_classifier_all_origins():
    c = ImportClassifier(
        stdlib=frozenset({"System"}),
        third_party=frozenset({"newtonsoft"}),
        internal=frozenset({"Acme"}),
    )
    assert c.classify("Acme") == "internal"
    assert c.classify("System") == "stdlib"
    assert c.classify("Newtonsoft") == "third_party"
    assert c.classify("Whatever") == "unknown"


def test_classifier_stdlib_beats_third_party():
    c = ImportClassifier(
        stdlib=frozenset({"System"}), third_party=frozenset({"system"})
    )
    assert c.classify("System") == "stdlib"


# ---------------------------------------------------------------------------
# Type declarations
# ---------------------------------------------------------------------------


def test_class_node_records_span_and_name_span():
    graph, _v, _f = _visit("public class Foo {}")
    node = _kind(graph, NodeKind.CLASS)[0]
    assert node.qualified_name == "Foo"
    assert node.metadata["visibility"] == "public"
    assert "name_span" in node.metadata
    assert node.span is not None


def test_type_flags():
    graph, _v, _f = _visit(
        "interface I {} struct S {} record R(int X); enum E { A }"
        " delegate void D(int x);"
    )
    by_q = {n.qualified_name: n for n in _kind(graph, NodeKind.CLASS)}
    assert by_q["I"].metadata["is_interface"]
    assert by_q["S"].metadata["is_struct"]
    assert by_q["R"].metadata["is_record"]
    assert by_q["E"].metadata["is_enum"]
    assert by_q["D"].metadata["is_delegate"]


def test_abstract_and_sealed_modifiers():
    graph, _v, _f = _visit(
        "public abstract class A {} internal sealed class B {}"
    )
    by_q = {n.qualified_name: n for n in _kind(graph, NodeKind.CLASS)}
    assert by_q["A"].metadata["is_abstract"]
    assert by_q["A"].metadata["visibility"] == "public"
    assert by_q["B"].metadata["is_sealed"]
    assert by_q["B"].metadata["visibility"] == "internal"


def test_base_list_records_heads_only():
    _g, visitor, _f = _visit("class C : Base, IRepo<Invoice> {}")
    base = [o for o in visitor.occurrences if o.role == "base"]
    assert len(base) == 2  # Base and IRepo — not the Invoice type argument


def test_record_primary_constructor_base_type():
    # record R(int Id) : Base(Id) wraps its base in
    # primary_constructor_base_type, not a bare identifier/qualified_name.
    _g, visitor, _f = _visit("record R(int Id) : Base(Id);")
    base = [o for o in visitor.occurrences if o.role == "base"]
    assert len(base) == 1


def test_record_without_body():
    graph, _v, _f = _visit("record R(int Id, decimal Amount);")
    assert "R" in _qnames(graph, NodeKind.CLASS)
    assert {"R.Id", "R.Amount"} <= _qnames(graph, NodeKind.PARAMETER)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


def test_method_constructor_and_params():
    graph, visitor, _f = _visit(
        "class C { public Invoice Get(int id) { return null; } "
        "public C(int seed) {} }"
    )
    assert {"C.Get", "C.C"} <= _qnames(graph, NodeKind.METHOD)
    assert {"C.Get.id", "C.C.seed"} <= _qnames(graph, NodeKind.PARAMETER)
    # Non-predefined return type Invoice becomes an annotation occurrence.
    assert any(o.role == "annotation" for o in visitor.occurrences)


def test_parameter_has_default():
    graph, _v, _f = _visit("class C { void M(int x = 5, int y = 0) {} }")
    by_name = {n.name: n for n in _kind(graph, NodeKind.PARAMETER)}
    assert by_name["x"].metadata["has_default"] is True


def test_parameter_without_default():
    graph, _v, _f = _visit("class C { void M(int x) {} }")
    by_name = {n.name: n for n in _kind(graph, NodeKind.PARAMETER)}
    assert by_name["x"].metadata["has_default"] is False


def test_method_kind_is_method_inside_class():
    graph, _v, _f = _visit("class C { void M(){} }")
    node = next(n for n in _kind(graph, NodeKind.METHOD) if n.name == "M")
    assert node.metadata["visibility"] == ""
    assert node.metadata["is_static"] is False


def test_local_function_is_function_node():
    graph, _v, _f = _visit(
        "class C { void M(){ int Local(int a){ return a; } Local(1); } }"
    )
    assert "C.M.Local" in _qnames(graph, NodeKind.FUNCTION)


def test_property_and_accessor_body():
    graph, visitor, _f = _visit(
        "class C { int _n; public int N { get { return Compute(); } } }"
    )
    assert "C.N" in _qnames(graph, NodeKind.ATTRIBUTE)
    node = next(n for n in _kind(graph, NodeKind.ATTRIBUTE) if n.name == "N")
    assert node.metadata["is_property"]
    assert "call" in _roles(visitor)


def test_property_expression_bodied():
    _g, visitor, _f = _visit("class C { int _n; public int N => Compute(); }")
    assert "call" in _roles(visitor)


def test_property_auto_accessors_no_body():
    graph, _v, _f = _visit("class C { public int N { get; set; } }")
    assert "C.N" in _qnames(graph, NodeKind.ATTRIBUTE)


def test_field_attribute_type_and_initializer():
    graph, visitor, _f = _visit("class C { private Invoice _inv = Make(); }")
    assert "C._inv" in _qnames(graph, NodeKind.ATTRIBUTE)
    assert "annotation" in _roles(visitor)  # Invoice
    assert "call" in _roles(visitor)  # Make()


def test_event_field():
    graph, _v, _f = _visit(
        "class C { public event System.EventHandler Changed; }"
    )
    node = next(
        n for n in _kind(graph, NodeKind.ATTRIBUTE) if n.name == "Changed"
    )
    assert node.metadata["is_event"]


def test_enum_members():
    graph, _v, _f = _visit("enum E { Open, Closed }")
    names = {n.name for n in _kind(graph, NodeKind.ATTRIBUTE)}
    assert {"Open", "Closed"} <= names


def test_operator_and_destructor():
    graph, _v, _f = _visit(
        "class C { public static C operator +(C a, C b) => a; ~C(){} }"
    )
    names = {n.name for n in _kind(graph, NodeKind.METHOD)}
    assert "+" in names
    assert "C" in names  # destructor


def test_two_operator_overloads_do_not_collide():
    # Both used to be named "operator" (the keyword, not the symbol field),
    # so they collapsed onto the same deterministic node ID.
    graph, _v, _f = _visit(
        "class C {"
        " public static C operator +(C a, C b) => a;"
        " public static C operator -(C a, C b) => a;"
        "}"
    )
    names = {n.name for n in _kind(graph, NodeKind.METHOD)}
    assert {"+", "-"} <= names


def test_delegate_annotations():
    _g, visitor, _f = _visit("delegate Invoice Make(Customer c);")
    assert len([o for o in visitor.occurrences if o.role == "annotation"]) >= 2


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def _imports(graph):
    return {
        n.metadata["original_name"]: n
        for n in _kind(graph, NodeKind.IMPORT)
    }


def test_using_plain_and_qualified():
    classifier = ImportClassifier(stdlib=frozenset({"System"}))
    graph, _v, _f = _visit(
        "using System; using System.Text;", classifier=classifier
    )
    imports = _imports(graph)
    assert imports["System"].metadata["origin"] == "stdlib"
    assert imports["System"].name == "System"
    assert imports["System.Text"].metadata["origin"] == "stdlib"
    assert imports["System.Text"].name == "Text"


def test_using_static():
    classifier = ImportClassifier(stdlib=frozenset({"System"}))
    graph, _v, _f = _visit("using static System.Math;", classifier=classifier)
    assert _imports(graph)["System.Math"].metadata["origin"] == "stdlib"


def test_using_alias():
    classifier = ImportClassifier(third_party=frozenset({"newtonsoft"}))
    graph, _v, _f = _visit(
        "using Json = Newtonsoft.Json;", classifier=classifier
    )
    node = _imports(graph)["Newtonsoft.Json"]
    assert node.metadata["alias"] == "Json"
    assert node.name == "Json"
    assert node.metadata["origin"] == "third_party"


def test_using_global():
    classifier = ImportClassifier(stdlib=frozenset({"System"}))
    graph, _v, _f = _visit(
        "global using System.Linq;", classifier=classifier
    )
    assert _imports(graph)["System.Linq"].metadata["origin"] == "stdlib"


def test_internal_import_resolves_to_module():
    classifier = ImportClassifier(internal=frozenset({"Acme"}))
    graph, _v, _f = _visit(
        "using Acme.Services;",
        classifier=classifier,
        modules={"Acme.Services": "module-id"},
    )
    resolves = [
        r for r in graph.relations if r.kind == RelationKind.RESOLVES_TO
    ]
    assert any(r.target_id == "module-id" for r in resolves)


def test_internal_import_falls_back_to_external_symbol():
    classifier = ImportClassifier(internal=frozenset({"Acme"}))
    graph, _v, _f = _visit(
        "using Acme.Missing;", classifier=classifier, modules={}
    )
    external = _kind(graph, NodeKind.EXTERNAL_SYMBOL)
    assert any(n.metadata["origin"] == "internal" for n in external)


def test_unknown_import_external_symbol():
    graph, _v, _f = _visit("using Some.Vendor.Lib;")
    external = _kind(graph, NodeKind.EXTERNAL_SYMBOL)
    assert any(n.metadata["origin"] == "unknown" for n in external)


# ---------------------------------------------------------------------------
# Value scanning (calls / reads / writes)
# ---------------------------------------------------------------------------


def test_invocation_read_write():
    _graph, visitor, _f = _visit(
        "class C { int count; void M(){ obj.Foo(count); this.count = 5;"
        " Bare(); } }"
    )
    roles = _roles(visitor)
    assert "call" in roles  # Foo, Bare
    assert "read" in roles  # obj, count
    assert "write" in roles  # count


def test_object_creation_is_call():
    _g, visitor, _f = _visit("class C { void M(){ new Foo(1); } }")
    assert "call" in _roles(visitor)


def test_generic_invocation():
    _g, visitor, _f = _visit("class C { void M(){ Helper.Do<int>(); } }")
    assert "call" in _roles(visitor)


def test_assignment_to_bare_identifier():
    _g, visitor, _f = _visit("class C { int n; void M(){ n = Compute(); } }")
    assert "call" in _roles(visitor)  # right-hand Compute()


def test_scan_receiver_parenthesized():
    _g, visitor, _f = _visit("class C { void M(){ (GetFn())(); } }")
    assert "call" in _roles(visitor)  # inner GetFn()


def test_nested_member_access_reads():
    _g, visitor, _f = _visit("class C { void M(){ var x = a.b.c; } }")
    assert _roles(visitor).count("read") >= 3  # a, b, c


def test_top_level_statement_attributed_to_file():
    _g, visitor, file_id = _visit('System.Console.WriteLine("hi");')
    calls = [o for o in visitor.occurrences if o.role == "call"]
    assert calls
    assert all(o.enclosing_id == file_id for o in calls)


# ---------------------------------------------------------------------------
# Type annotation shapes
# ---------------------------------------------------------------------------


def test_generic_and_qualified_type_annotation():
    _g, visitor, _f = _visit(
        "class C { System.Collections.Generic.List<Invoice> Items()"
        " { return null; } }"
    )
    assert "annotation" in _roles(visitor)


def test_nullable_type_annotation():
    _g, visitor, _f = _visit("class C { Invoice? Find(){ return null; } }")
    assert "annotation" in _roles(visitor)


def test_array_type_annotation():
    _g, visitor, _f = _visit("class C { Invoice[] All(){ return null; } }")
    assert "annotation" in _roles(visitor)


# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------


def test_file_scoped_namespace_scope():
    graph, _v, _f = _visit("namespace A.B; class C { void M(){} }")
    assert "A.B.C" in _qnames(graph, NodeKind.CLASS)
    assert "A.B.C.M" in _qnames(graph, NodeKind.METHOD)


def test_nested_block_namespaces():
    graph, _v, _f = _visit("namespace A { namespace B { class C {} } }")
    assert "A.B.C" in _qnames(graph, NodeKind.CLASS)


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


def test_interface_method_has_no_body():
    graph, _v, _f = _visit("interface I { int Get(int id); }")
    assert "I.Get" in _qnames(graph, NodeKind.METHOD)
    assert "I.Get.id" in _qnames(graph, NodeKind.PARAMETER)


def test_expression_bodied_accessor():
    _g, visitor, _f = _visit(
        "class C { int _n; public int N { get => Compute(); } }"
    )
    assert "call" in _roles(visitor)


# ---------------------------------------------------------------------------
# UTF-16 column conversion
# ---------------------------------------------------------------------------


def test_to_utf16_col_ascii_line_is_identity():
    _g, visitor, _f = _visit("class C {}")
    assert visitor._to_utf16_col(0, 5) == 5


def test_to_utf16_col_converts_non_ascii_prefix():
    # "é" is 2 bytes in UTF-8 but 1 code unit in UTF-16 — a byte column and
    # a UTF-16 column diverge as soon as one appears earlier on the line.
    source = "var éé = 1;"
    _g, visitor, _f = _visit(source)
    byte_col = len("var éé".encode())
    assert visitor._to_utf16_col(0, byte_col) == len("var éé")


def test_occurrence_column_uses_utf16_not_byte_offset():
    # A non-ASCII identifier earlier on the same line must not shift later
    # occurrences' columns when byte-width diverges from UTF-16 width —
    # Roslyn/SCIP/LSP positions are UTF-16 code-unit offsets, tree-sitter's
    # raw start_point columns are UTF-8 byte offsets.
    source = "class C { void M() { var éé = 1; Foo(); } }"
    _g, visitor, _f = _visit(source)
    foo_occ = next(o for o in visitor.occurrences if o.role == "call")
    prefix = source[: source.index("Foo(")]
    expected_col = len(prefix.encode("utf-16-le")) // 2 + 1
    assert foo_occ.col == expected_col


def test_new_predefined_type_has_no_call_target():
    # new string(...) — the type is a predefined_type with no name leaf, so
    # no call occurrence for the ctor, but arguments are still scanned.
    _g, visitor, _f = _visit(
        "class C { void M(){ var s = new string('a', G()); } }"
    )
    assert "call" in _roles(visitor)  # the G() argument


def test_object_initializer_without_parens():
    # new Foo { } has no argument list — _scan_arguments must tolerate that.
    _g, visitor, _f = _visit("class C { void M(){ var f = new Foo { }; } }")
    assert any(o.role == "call" for o in visitor.occurrences)  # Foo


def test_object_initializer_contents_are_scanned():
    _g, visitor, _f = _visit(
        "class C { void M(){ var f = new Foo { X = Bar() }; } }"
    )
    assert "call" in _roles(visitor)  # Bar()
    assert "read" in _roles(visitor)  # X


def test_duplicate_import_reuses_external_symbol():
    graph, _v, _f = _visit("using A.B.C; using A.B.C;")
    externals = [
        n
        for n in _kind(graph, NodeKind.EXTERNAL_SYMBOL)
        if n.qualified_name == "A.B.C"
    ]
    assert len(externals) == 1  # created once, reused on the second using


def test_overloaded_methods_share_one_node():
    graph, _v, _f = _visit("class C { void M(int a){} void M(string b){} }")
    methods = [
        n
        for n in _kind(graph, NodeKind.METHOD)
        if n.qualified_name == "C.M"
    ]
    assert len(methods) == 1  # same qualified name → deduped


def test_qualified_base_type():
    _g, visitor, _f = _visit("class C : System.Exception {}")
    assert any(o.role == "base" for o in visitor.occurrences)


def test_unqualified_generic_field_annotation():
    _g, visitor, _f = _visit("class C { List<Invoice> _items; }")
    assert "annotation" in _roles(visitor)  # List and Invoice


def test_visitor_emits_no_semantic_edges():
    graph, _v, _f = _visit("class C : B { Invoice x; void M(){ Foo(); } }")
    kinds = {r.kind for r in graph.relations}
    assert RelationKind.CALLS not in kinds
    assert RelationKind.INHERITS_FROM not in kinds
    assert RelationKind.HAS_TYPE not in kinds
    assert RelationKind.REFERENCES not in kinds
    assert kinds <= {
        RelationKind.DECLARES,
        RelationKind.IMPORTS,
        RelationKind.RESOLVES_TO,
    }
