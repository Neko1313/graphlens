"""Unit tests for the PHP CST visitor."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from graphlens import GraphLens, Node, NodeKind, RelationKind
from graphlens.utils import make_node_id

from graphlens_php._visitor import (
    ImportClassifier,
    PhpASTVisitor,
    VisitorContext,
    extract_namespace,
    parse_php,
)

PROJECT = "acme/demo"


def _run(
    source: str,
    namespace: str = "App",
    classifier: ImportClassifier | None = None,
) -> tuple[GraphLens, PhpASTVisitor]:
    graph = GraphLens()
    src = source.encode()
    file_id = make_node_id(PROJECT, "f.php", NodeKind.FILE.value)
    graph.add_node(
        Node(id=file_id, kind=NodeKind.FILE, qualified_name="f.php", name="f")
    )
    ctx = VisitorContext(PROJECT, Path("f.php"), namespace)
    visitor = PhpASTVisitor(ctx, graph, file_id, src, classifier)
    visitor.visit(parse_php(src).root_node)
    return graph, visitor


def _kinds(graph: GraphLens) -> Counter:
    return Counter(n.kind.name for n in graph.nodes.values())


def _roles(visitor: PhpASTVisitor) -> Counter:
    return Counter(o.role for o in visitor.occurrences)


def _node(graph: GraphLens, qname: str, kind: NodeKind) -> Node:
    nid = make_node_id(PROJECT, qname, kind.value)
    return graph.nodes[nid]


# ---------------------------------------------------------------------------
# extract_namespace
# ---------------------------------------------------------------------------


def test_extract_namespace_present():
    root = parse_php(b"<?php\nnamespace App\\Service;\nclass C {}").root_node
    assert extract_namespace(root) == "App\\Service"


def test_extract_namespace_global():
    root = parse_php(b"<?php\nclass C {}").root_node
    assert extract_namespace(root) == ""


def test_extract_namespace_block_form_without_name():
    root = parse_php(b"<?php\nnamespace { class C {} }").root_node
    assert extract_namespace(root) == ""


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


def test_class_interface_trait_enum():
    graph, _ = _run(
        "<?php\n"
        "abstract class A {}\n"
        "interface I {}\n"
        "trait T {}\n"
        "enum E: string { case X = 'x'; }\n"
    )
    a = _node(graph, "App\\A", NodeKind.CLASS)
    assert a.metadata["is_abstract"] is True
    assert _node(graph, "App\\I", NodeKind.CLASS).metadata["is_interface"]
    assert _node(graph, "App\\T", NodeKind.CLASS).metadata["is_trait"]
    e = _node(graph, "App\\E", NodeKind.CLASS)
    assert e.metadata["is_enum"] is True
    # enum case becomes an ATTRIBUTE
    case = _node(graph, "App\\E\\X", NodeKind.ATTRIBUTE)
    assert case.metadata["is_enum_case"] is True


def test_class_global_namespace_qualifies_without_prefix():
    graph, _ = _run("<?php\nclass C {}", namespace="")
    assert _node(graph, "C", NodeKind.CLASS)


def test_base_and_interface_and_trait_occurrences():
    _, visitor = _run(
        "<?php\n"
        "class C extends Base implements I, J {\n"
        "    use SomeTrait;\n"
        "}\n"
    )
    # Base + I + J + SomeTrait = 4 base occurrences
    assert _roles(visitor)["base"] == 4


def test_method_vs_function_and_modifiers():
    graph, _ = _run(
        "<?php\n"
        "function topLevel(): void {}\n"
        "class C {\n"
        "    public static function s(): void {}\n"
        "    private function p(): void {}\n"
        "}\n"
    )
    assert _node(graph, "App\\topLevel", NodeKind.FUNCTION)
    s = _node(graph, "App\\C\\s", NodeKind.METHOD)
    assert s.metadata["is_static"] is True
    assert s.metadata["visibility"] == "public"
    p = _node(graph, "App\\C\\p", NodeKind.METHOD)
    assert p.metadata["visibility"] == "private"


def test_parameters_variants():
    graph, visitor = _run(
        "<?php\n"
        "class C {\n"
        "    public function m(\n"
        "        int $a,\n"
        "        ?User $b = null,\n"
        "        string ...$rest,\n"
        "        private Logger $log\n"
        "    ): void {}\n"
        "}\n"
    )
    a = _node(graph, "App\\C\\m\\a", NodeKind.PARAMETER)
    assert a.metadata["has_default"] is False
    b = _node(graph, "App\\C\\m\\b", NodeKind.PARAMETER)
    assert b.metadata["has_default"] is True
    rest = _node(graph, "App\\C\\m\\rest", NodeKind.PARAMETER)
    assert rest.metadata["is_variadic"] is True
    log = _node(graph, "App\\C\\m\\log", NodeKind.PARAMETER)
    assert log.metadata["is_promoted"] is True
    # User + Logger types → annotation occurrences (int/string/void skipped)
    assert _roles(visitor)["annotation"] == 2


def test_property_typed_and_untyped():
    graph, _ = _run(
        "<?php\n"
        "class C {\n"
        "    private int $count = 0;\n"
        "    public $loose, $second;\n"
        "}\n"
    )
    count = _node(graph, "App\\C\\count", NodeKind.ATTRIBUTE)
    assert count.metadata["visibility"] == "private"
    assert _node(graph, "App\\C\\loose", NodeKind.ATTRIBUTE)
    assert _node(graph, "App\\C\\second", NodeKind.ATTRIBUTE)


def test_const_class_vs_toplevel():
    graph, _ = _run(
        "<?php\n"
        "const TOP = 1;\n"
        "class C {\n"
        "    public const FOO = 2;\n"
        "}\n"
    )
    assert _node(graph, "App\\TOP", NodeKind.VARIABLE)
    assert _node(graph, "App\\C\\FOO", NodeKind.ATTRIBUTE)


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def _classifier() -> ImportClassifier:
    return ImportClassifier(
        stdlib=frozenset({"DateTime"}),
        third_party=frozenset({"monolog"}),
        internal=frozenset({"App"}),
    )


def test_imports_origins_and_alias():
    graph, _ = _run(
        "<?php\n"
        "namespace App;\n"
        "use App\\Model\\User;\n"
        "use Monolog\\Logger as Log;\n"
        "use DateTime;\n"
        "use Vendor\\Unknown\\Thing;\n",
        classifier=_classifier(),
    )
    imports = {
        n.metadata["original_name"]: n
        for n in graph.nodes.values()
        if n.kind == NodeKind.IMPORT
    }
    assert imports["App\\Model\\User"].metadata["origin"] == "internal"
    log = imports["Monolog\\Logger"]
    assert log.metadata["origin"] == "third_party"
    assert log.metadata["alias"] == "Log"
    assert imports["App\\Model\\User"].metadata["alias"] is None
    assert imports["DateTime"].metadata["origin"] == "stdlib"
    assert imports["Vendor\\Unknown\\Thing"].metadata["origin"] == "unknown"


def test_import_group():
    graph, _ = _run(
        "<?php\n"
        "namespace App;\n"
        "use App\\{Foo, Bar as B};\n",
        classifier=_classifier(),
    )
    names = {
        n.metadata["original_name"]
        for n in graph.nodes.values()
        if n.kind == NodeKind.IMPORT
    }
    assert "App\\Foo" in names
    assert "App\\Bar" in names


def test_internal_import_resolves_to_module():
    graph = GraphLens()
    # Pre-seed the App\Model module node so the import RESOLVES_TO it.
    mod_id = make_node_id(PROJECT, "App\\Model", NodeKind.MODULE.value)
    graph.add_node(
        Node(
            id=mod_id,
            kind=NodeKind.MODULE,
            qualified_name="App\\Model",
            name="Model",
        )
    )
    src = b"<?php\nnamespace App;\nuse App\\Model\\User;\n"
    file_id = make_node_id(PROJECT, "f.php", NodeKind.FILE.value)
    graph.add_node(
        Node(id=file_id, kind=NodeKind.FILE, qualified_name="f.php", name="f")
    )
    ctx = VisitorContext(PROJECT, Path("f.php"), "App")
    visitor = PhpASTVisitor(
        ctx,
        graph,
        file_id,
        src,
        _classifier(),
        modules={"App\\Model": mod_id},
    )
    visitor.visit(parse_php(src).root_node)
    # RESOLVES_TO points to the existing MODULE, not an EXTERNAL_SYMBOL
    targets = [
        r.target_id
        for r in graph.relations
        if r.kind == RelationKind.RESOLVES_TO
    ]
    assert mod_id in targets


# ---------------------------------------------------------------------------
# Value scanning / occurrences
# ---------------------------------------------------------------------------


def test_call_occurrences_all_kinds():
    _, visitor = _run(
        "<?php\n"
        "class C {\n"
        "    public function m(): void {\n"
        "        foo();\n"
        "        ns\\bar();\n"
        "        $this->method();\n"
        "        $this?->nullsafe();\n"
        "        Helper::make();\n"
        "        new Widget();\n"
        "        $cb();\n"
        "        new $dynamic();\n"
        "    }\n"
        "}\n"
    )
    # foo, bar, method, nullsafe, make, Widget = 6 calls
    # ($cb() and new $dynamic() have no resolvable name → no call occurrence)
    assert _roles(visitor)["call"] == 6


def test_read_write_and_access_occurrences():
    _, visitor = _run(
        "<?php\n"
        "class C {\n"
        "    public function m(): void {\n"
        "        $this->prop = 1;\n"
        "        $x = $this->other;\n"
        "        $y = $this->$dynamic;\n"
        "        $z = Config::SETTING;\n"
        "        $w = Foo::class;\n"
        "        $v = GLOBAL_CONST;\n"
        "        $u = ns\\OTHER_CONST;\n"
        "    }\n"
        "}\n"
    )
    roles = _roles(visitor)
    assert roles["write"] == 1  # $this->prop =
    # other, SETTING, GLOBAL_CONST, ns\OTHER_CONST → reads (Foo::class & $dynamic skipped)
    assert roles["read"] >= 4


def test_arguments_are_scanned():
    _, visitor = _run(
        "<?php\n"
        "function m(): void {\n"
        "    foo(bar(), BAZ);\n"
        "}\n"
    )
    roles = _roles(visitor)
    assert roles["call"] == 2  # foo + bar
    assert roles["read"] == 1  # BAZ


def test_toplevel_expression_statement():
    _, visitor = _run("<?php\nnamespace App;\nrun_app();\n")
    assert _roles(visitor)["call"] == 1


def test_union_and_qualified_type_annotations():
    _, visitor = _run(
        "<?php\n"
        "function f(Foo|Bar $x, \\Deep\\Qualified $y): void {}\n"
    )
    # Foo, Bar, Qualified → 3 annotation occurrences
    assert _roles(visitor)["annotation"] == 3


# ---------------------------------------------------------------------------
# Visitor never emits resolution edges
# ---------------------------------------------------------------------------


def test_function_without_return_type_and_untyped_param():
    graph, visitor = _run("<?php\nfunction f($a) { return $a; }\n")
    assert _node(graph, "App\\f", NodeKind.FUNCTION)
    assert _node(graph, "App\\f\\a", NodeKind.PARAMETER)
    # no type anywhere → no annotation occurrences
    assert _roles(visitor)["annotation"] == 0


def test_method_without_body():
    graph, _ = _run(
        "<?php\n"
        "abstract class C {\n"
        "    abstract public function todo(): void;\n"
        "}\n"
    )
    assert _node(graph, "App\\C\\todo", NodeKind.METHOD)


def test_nested_function_in_body():
    graph, _ = _run(
        "<?php\nfunction outer() { function inner() {} }\n"
    )
    assert _node(graph, "App\\outer", NodeKind.FUNCTION)
    # inner is visited (qualified under outer's scope)
    assert _node(graph, "App\\outer\\inner", NodeKind.FUNCTION)


def test_dynamic_method_call_records_no_call():
    _, visitor = _run(
        "<?php\nfunction m() { $obj->$dynamic(); }\n"
    )
    assert _roles(visitor)["call"] == 0


def test_dynamic_property_write_records_no_write():
    _, visitor = _run(
        "<?php\nclass C { function m() { $this->$dynamic = 1; } }\n"
    )
    assert _roles(visitor)["write"] == 0


def test_duplicate_declaration_is_deduped():
    # Parser tolerates a redeclared property; the second add is a no-op.
    graph, _ = _run(
        "<?php\nclass C { public $a; public $a; }\n"
    )
    assert _node(graph, "App\\C\\a", NodeKind.ATTRIBUTE)


def test_duplicate_external_import_reuses_symbol():
    graph, _ = _run(
        "<?php\n"
        "namespace App;\n"
        "use Vendor\\Thing;\n"
        "use Vendor\\Thing as T;\n",
        classifier=_classifier(),
    )
    ext = [
        n
        for n in graph.nodes.values()
        if n.kind == NodeKind.EXTERNAL_SYMBOL
        and n.qualified_name == "Vendor\\Thing"
    ]
    assert len(ext) == 1


def test_visitor_emits_no_resolution_edges():
    graph, _ = _run(
        "<?php\n"
        "class C extends Base {\n"
        "    public function m(): void { foo(); }\n"
        "}\n"
    )
    kinds = {r.kind for r in graph.relations}
    assert RelationKind.CALLS not in kinds
    assert RelationKind.INHERITS_FROM not in kinds
    assert RelationKind.HAS_TYPE not in kinds
    assert RelationKind.REFERENCES not in kinds
    # but DECLARES / IMPORTS structure is present
    assert RelationKind.DECLARES in kinds
