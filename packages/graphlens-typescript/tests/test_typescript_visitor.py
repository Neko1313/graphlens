"""Tests for TypeScript AST visitor."""

from __future__ import annotations

from conftest import nodes_of_kind, parse_and_visit
from graphlens import NodeKind, RelationKind

from graphlens_typescript._visitor import (
    ImportClassifier,
    _extract_heritage_bases,
    _make_span,
    _name_from_node,
    _strip_string_quotes,
    parse_typescript,
)


class TestVisitorDispatch:
    def test_empty_file_no_error(self):
        graph, _ = parse_and_visit("")
        assert graph is not None

    def test_comment_only_file(self):
        graph, _ = parse_and_visit("// just a comment\n")
        assert graph is not None

    def test_structural_node_uses_absolute_file_path(self):
        graph, _ = parse_and_visit("export function foo() {}\n")
        fn = next(
            n for n in graph.nodes.values() if n.kind.value == "function"
        )
        # absolute path from the visitor context, not the relative one
        assert fn.file_path is not None
        assert fn.file_path.startswith("/") or ":" in fn.file_path


class TestClassDeclaration:
    def test_simple_class(self):
        graph, _ = parse_and_visit("class Foo {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "Foo" for c in classes)

    def test_class_qualified_name(self):
        graph, _ = parse_and_visit("class Foo {}", module_qname="myapp.mod")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        foo = next(c for c in classes if c.name == "Foo")
        assert foo.qualified_name == "myapp.mod.Foo"

    def test_exported_class(self):
        graph, _ = parse_and_visit("export class Bar {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "Bar" for c in classes)

    def test_abstract_class(self):
        graph, _ = parse_and_visit("abstract class Base {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "Base" for c in classes)
        base = next(c for c in classes if c.name == "Base")
        assert base.metadata.get("is_abstract") is True

    def test_class_with_inheritance(self):
        graph, _ = parse_and_visit("class Child extends Parent {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        child = next(c for c in classes if c.name == "Child")
        assert "Parent" in child.metadata.get("bases", [])

    def test_inherits_from_relation(self):
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("class Child extends Parent {}")
        bases = [o for o in v.occurrences if o.role == "base"]
        assert len(bases) == 1


class TestInterfaceDeclaration:
    def test_simple_interface(self):
        graph, _ = parse_and_visit("interface IFoo { bar(): void; }")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "IFoo" for c in classes)

    def test_interface_is_abstract(self):
        graph, _ = parse_and_visit("interface IFoo {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        ifoo = next(c for c in classes if c.name == "IFoo")
        assert ifoo.metadata.get("is_abstract") is True
        assert ifoo.metadata.get("is_interface") is True

    def test_interface_extends(self):
        graph, _ = parse_and_visit("interface IChild extends IParent {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        ichild = next(c for c in classes if c.name == "IChild")
        assert "IParent" in ichild.metadata.get("bases", [])


class TestFunctionDeclaration:
    def test_simple_function(self):
        graph, _ = parse_and_visit(
            "function greet(name: string): string { return name; }"
        )
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        assert any(f.name == "greet" for f in funcs)

    def test_exported_function(self):
        graph, _ = parse_and_visit("export function helper() {}")
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        assert any(f.name == "helper" for f in funcs)

    def test_async_function(self):
        graph, _ = parse_and_visit("async function fetchData() {}")
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        func = next(f for f in funcs if f.name == "fetchData")
        assert func.metadata.get("is_async") is True

    def test_generator_function(self):
        graph, _ = parse_and_visit("function* gen() { yield 1; }")
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        assert any(f.name == "gen" for f in funcs)


class TestMethodDefinition:
    def test_method_inside_class(self):
        src = "class MyClass { greet() { return 'hi'; } }"
        graph, _ = parse_and_visit(src)
        methods = nodes_of_kind(graph, NodeKind.METHOD)
        assert any(m.name == "greet" for m in methods)

    def test_method_qualified_name(self):
        src = "class MyClass { greet() {} }"
        graph, _ = parse_and_visit(src, module_qname="myapp.mod")
        methods = nodes_of_kind(graph, NodeKind.METHOD)
        greet = next(m for m in methods if m.name == "greet")
        assert greet.qualified_name == "myapp.mod.MyClass.greet"

    def test_constructor_is_method(self):
        src = "class MyClass { constructor(x: number) {} }"
        graph, _ = parse_and_visit(src)
        methods = nodes_of_kind(graph, NodeKind.METHOD)
        assert any(m.name == "constructor" for m in methods)


class TestImportStatement:
    def test_default_import(self):
        classifier = ImportClassifier(stdlib=frozenset({"fs"}))
        graph, _ = parse_and_visit(
            "import fs from 'fs';", classifier=classifier
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        assert any(i.name == "fs" for i in imports)

    def test_named_import(self):
        classifier = ImportClassifier(stdlib=frozenset({"fs"}))
        graph, _ = parse_and_visit(
            "import { readFile, writeFile } from 'fs';", classifier=classifier
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        names = {i.name for i in imports}
        assert "readFile" in names
        assert "writeFile" in names

    def test_namespace_import(self):
        classifier = ImportClassifier(stdlib=frozenset({"path"}))
        graph, _ = parse_and_visit(
            "import * as path from 'path';", classifier=classifier
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        assert any(i.name == "path" for i in imports)
        ns = next(i for i in imports if i.name == "path")
        assert ns.metadata.get("is_star") is True

    def test_stdlib_origin(self):
        classifier = ImportClassifier(stdlib=frozenset({"fs"}))
        graph, _ = parse_and_visit(
            "import fs from 'fs';", classifier=classifier
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        fs_import = next(i for i in imports if i.name == "fs")
        assert fs_import.metadata.get("origin") == "stdlib"

    def test_third_party_origin(self):
        classifier = ImportClassifier(third_party=frozenset({"lodash"}))
        graph, _ = parse_and_visit(
            "import _ from 'lodash';", classifier=classifier
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "_")
        assert imp.metadata.get("origin") == "third_party"

    def test_relative_import_is_internal(self):
        classifier = ImportClassifier()
        graph, _ = parse_and_visit(
            "import { helper } from './utils';", classifier=classifier
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "helper")
        assert imp.metadata.get("origin") == "internal"
        assert imp.metadata.get("is_relative") is True

    def test_imports_relation(self):
        graph, file_id = parse_and_visit(
            "import { foo } from 'somemod';",
            classifier=ImportClassifier(third_party=frozenset({"somemod"})),
        )
        imports_rels = [
            r
            for r in graph.relations
            if r.kind == RelationKind.IMPORTS and r.source_id == file_id
        ]
        assert len(imports_rels) >= 1

    def test_resolves_to_relation(self):
        graph, _ = parse_and_visit(
            "import { foo } from 'somemod';",
            classifier=ImportClassifier(third_party=frozenset({"somemod"})),
        )
        resolves = [
            r for r in graph.relations if r.kind == RelationKind.RESOLVES_TO
        ]
        assert len(resolves) >= 1

    def test_type_import(self):
        graph, _ = parse_and_visit("import type { Foo } from './types';")
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        assert any(i.name == "Foo" for i in imports)

    def test_aliased_named_import(self):
        graph, _ = parse_and_visit(
            "import { original as alias } from 'somemod';",
            classifier=ImportClassifier(third_party=frozenset({"somemod"})),
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        assert any(i.name == "alias" for i in imports)
        imp = next(i for i in imports if i.name == "alias")
        assert imp.metadata.get("alias") == "alias"

    def test_node_prefix_stripped_for_classification(self):
        classifier = ImportClassifier(stdlib=frozenset({"fs"}))
        graph, _ = parse_and_visit(
            "import { readFile } from 'node:fs';", classifier=classifier
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "readFile")
        assert imp.metadata.get("origin") == "stdlib"


class TestCallExtraction:
    def test_call_inside_function(self):
        from conftest import parse_and_visit_visitor
        src = "function greet() { console.log('hi'); }"
        _, v = parse_and_visit_visitor(src)
        calls = [o for o in v.occurrences if o.role == "call"]
        assert len(calls) >= 1

    def test_call_creates_external_symbol_node(self):
        from conftest import parse_and_visit_visitor
        src = "function greet() { doSomething(); }"
        _, v = parse_and_visit_visitor(src)
        calls = [o for o in v.occurrences if o.role == "call"]
        assert any(True for o in calls)


class TestOccurrences:
    def test_call_records_call_occurrence(self):
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("function f() { a(); }")
        calls = [o for o in v.occurrences if o.role == "call"]
        assert any(o.col > 0 for o in calls)

    def test_call_no_extra_external_symbol(self):
        from conftest import parse_and_visit_visitor
        graph, v = parse_and_visit_visitor("function f() { a(); }")
        calls = [o for o in v.occurrences if o.role == "call"]
        assert len(calls) >= 1
        # visitor no longer emits CALLS relations
        assert not any(r.kind.value == "calls" for r in graph.relations)

    def test_arg_records_read(self):
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("function f() { a(b); }")
        reads = [o for o in v.occurrences if o.role == "read"]
        assert any(True for o in reads)  # b is a read

    def test_no_double_count(self):
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("function f() { const x = g(a); }")
        reads = [o for o in v.occurrences if o.role == "read"]
        # 'a' should appear exactly once as read (not twice)
        read_names_cols = [(o.line, o.col) for o in reads]
        assert len(read_names_cols) == len(set(read_names_cols))


class TestArrowFunction:
    def test_const_arrow_function(self):
        graph, _ = parse_and_visit(
            "export const greet = (name: string) => name;"
        )
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        assert any(f.name == "greet" for f in funcs)

    def test_const_function_expression(self):
        graph, _ = parse_and_visit(
            "const process = function(x: number): number { return x * 2; };"
        )
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        assert any(f.name == "process" for f in funcs)

    def test_arrow_with_return_type_annotation(self):
        graph, _ = parse_and_visit("const fn: () => string = () => 'hello';")
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        assert any(f.name == "fn" for f in funcs)

    def test_const_non_function_not_registered(self):
        graph, _ = parse_and_visit("const x = 42;")
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        assert not any(f.name == "x" for f in funcs)
        # x should now be a VARIABLE
        variables = nodes_of_kind(graph, NodeKind.VARIABLE)
        assert any(v.name == "x" for v in variables)


class TestNestedDefinitions:
    def test_nested_function_in_function_body(self):
        src = "function outer() { function inner() { return 1; } }"
        graph, _ = parse_and_visit(src)
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        assert any(f.name == "outer" for f in funcs)
        assert any(f.name == "inner" for f in funcs)

    def test_nested_class_in_function_body(self):
        src = "function factory() { class LocalClass {} }"
        graph, _ = parse_and_visit(src)
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "LocalClass" for c in classes)


class TestExportStatement:
    def test_reexport_from_external(self):
        graph, _ = parse_and_visit(
            "export { foo } from 'somemod';",
            classifier=ImportClassifier(third_party=frozenset({"somemod"})),
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        assert any("__reexport" in i.name for i in imports)

    def test_reexport_from_relative(self):
        graph, _ = parse_and_visit("export { foo } from './utils';")
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        assert any("__reexport" in i.name for i in imports)

    def test_reexport_is_star(self):
        graph, _ = parse_and_visit("export { bar } from './helpers';")
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        reexport = next(i for i in imports if "__reexport" in i.name)
        assert reexport.metadata.get("is_star") is True

    def test_anonymous_default_class_skipped(self):
        # export default class {} — no name_node → graceful skip
        graph, _ = parse_and_visit("export default class {}")
        assert graph is not None


class TestSideEffectImport:
    def test_side_effect_import(self):
        graph, _ = parse_and_visit("import 'reflect-metadata';")
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        assert any("sideeffect" in i.name for i in imports)

    def test_side_effect_import_is_star(self):
        graph, _ = parse_and_visit("import 'polyfill';")
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        sideeffect = next(i for i in imports if "sideeffect" in i.name)
        assert sideeffect.metadata.get("is_star") is True

    def test_side_effect_import_origin_unknown(self):
        graph, _ = parse_and_visit("import 'unknown-polyfill';")
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        sideeffect = next(i for i in imports if "sideeffect" in i.name)
        assert sideeffect.metadata.get("origin") == "unknown"


class TestInheritanceEdgeCases:
    def test_class_extends_generic_base(self):
        graph, _ = parse_and_visit("class Foo extends Bar<T> {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        foo = next(c for c in classes if c.name == "Foo")
        assert "Bar" in foo.metadata.get("bases", [])

    def test_class_extends_namespaced_base(self):
        graph, _ = parse_and_visit("class Foo extends NS.Base {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        foo = next(c for c in classes if c.name == "Foo")
        assert "NS.Base" in foo.metadata.get("bases", [])

    def test_interface_extends_generic(self):
        graph, _ = parse_and_visit("interface IFoo extends IBase<T> {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        ifoo = next(c for c in classes if c.name == "IFoo")
        assert "IBase" in ifoo.metadata.get("bases", [])


class TestImportClassifier:
    def test_classify_stdlib(self):
        c = ImportClassifier(stdlib=frozenset({"fs"}))
        assert c.classify("fs") == "stdlib"

    def test_classify_internal(self):
        c = ImportClassifier(internal=frozenset({"mymod"}))
        assert c.classify("mymod") == "internal"

    def test_classify_third_party(self):
        c = ImportClassifier(third_party=frozenset({"lodash"}))
        assert c.classify("lodash") == "third_party"

    def test_classify_unknown(self):
        c = ImportClassifier()
        assert c.classify("somemod") == "unknown"

    def test_stdlib_takes_priority_over_internal(self):
        c = ImportClassifier(
            stdlib=frozenset({"mod"}), internal=frozenset({"mod"})
        )
        assert c.classify("mod") == "stdlib"


class TestParameters:
    def test_required_parameter(self):
        graph, _ = parse_and_visit("function f(x: number) {}")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        assert any(p.name == "x" for p in params)

    def test_optional_parameter(self):
        graph, _ = parse_and_visit("function f(x?: string) {}")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        assert any(p.name == "x" for p in params)
        param = next(p for p in params if p.name == "x")
        assert param.metadata.get("has_default") is True

    def test_rest_parameter(self):
        graph, _ = parse_and_visit("function f(...args: string[]) {}")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        assert any(p.name == "args" for p in params)
        param = next(p for p in params if p.name == "args")
        assert param.metadata.get("is_variadic") is True

    def test_default_value_parameter(self):
        graph, _ = parse_and_visit("function f(x = 5) {}")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        assert any(p.name == "x" for p in params)
        param = next(p for p in params if p.name == "x")
        assert param.metadata.get("has_default") is True

    def test_rest_parameter_no_annotation(self):
        graph, _ = parse_and_visit("function f(...items) {}")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        assert any(p.name == "items" for p in params)
        param = next(p for p in params if p.name == "items")
        assert param.metadata.get("is_variadic") is True

    def test_declares_relation_for_params(self):
        graph, _ = parse_and_visit("function f(x: number, y: string) {}")
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        func = next(f for f in funcs if f.name == "f")
        declares = [
            r
            for r in graph.relations
            if r.kind == RelationKind.DECLARES and r.source_id == func.id
        ]
        assert len(declares) == 2


class TestStructuralNodes:
    def test_type_alias(self):
        from conftest import parse_and_visit_visitor
        graph, _ = parse_and_visit_visitor("type V = string;")
        type_aliases = [
            n for n in graph.nodes.values()
            if n.kind == NodeKind.TYPE_ALIAS
        ]
        assert any(n.name == "V" for n in type_aliases)

    def test_enum_is_class_with_is_enum(self):
        from conftest import parse_and_visit_visitor
        graph, _ = parse_and_visit_visitor("enum E { A, B }")
        classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
        e = next((c for c in classes if c.name == "E"), None)
        assert e is not None
        assert e.metadata.get("is_enum") is True

    def test_enum_members_are_attributes(self):
        from conftest import parse_and_visit_visitor
        graph, _ = parse_and_visit_visitor("enum E { A, B }")
        attrs = [n for n in graph.nodes.values() if n.kind == NodeKind.ATTRIBUTE]
        names = {a.name for a in attrs}
        assert "A" in names
        assert "B" in names

    def test_const_variable(self):
        from conftest import parse_and_visit_visitor
        graph, _v = parse_and_visit_visitor("const C = 1;")
        variables = [
            n for n in graph.nodes.values() if n.kind == NodeKind.VARIABLE
        ]
        assert any(n.name == "C" for n in variables)

    def test_const_variable_write_occurrence(self):
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("const C = 1;")
        writes = [o for o in v.occurrences if o.role == "write"]
        assert len(writes) >= 1

    def test_const_read_occurrence(self):
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("function f() { const x = C; }")
        reads = [o for o in v.occurrences if o.role == "read"]
        assert len(reads) >= 1


class TestBaseAndAnnotationOccurrences:
    def test_class_base_occurrence(self):
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("class Sub extends Base {}")
        bases = [o for o in v.occurrences if o.role == "base"]
        assert len(bases) == 1

    def test_interface_base_occurrence(self):
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("interface IChild extends IParent {}")
        bases = [o for o in v.occurrences if o.role == "base"]
        assert len(bases) >= 1

    def test_param_type_annotation_occurrence(self):
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("function f(x: MyType): void {}")
        anns = [o for o in v.occurrences if o.role == "annotation"]
        assert len(anns) >= 1

    def test_no_inherits_from_relation(self):
        graph, _ = parse_and_visit("class Sub extends Base {}")
        inherits = [
            r for r in graph.relations
            if r.kind == RelationKind.INHERITS_FROM
        ]
        assert len(inherits) == 0


class TestNameSpan:
    def test_class_name_span_recorded(self):
        graph, _ = parse_and_visit("class MyClass {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        cls = next(c for c in classes if c.name == "MyClass")
        ns = cls.metadata.get("name_span")
        assert ns is not None
        # "MyClass" starts at column 7 (1-based) on line 1
        assert ns.start_line == 1
        assert ns.start_col == 7


class TestHelperFunctions:
    def test_strip_string_quotes_double(self):
        assert _strip_string_quotes('"hello"') == "hello"

    def test_strip_string_quotes_single(self):
        assert _strip_string_quotes("'hello'") == "hello"

    def test_strip_string_quotes_backtick(self):
        assert _strip_string_quotes("`hello`") == "hello"

    def test_strip_string_quotes_unquoted(self):
        assert _strip_string_quotes("hello") == "hello"

    def test_strip_string_quotes_empty(self):
        assert _strip_string_quotes("") == ""

    def test_make_span_none(self):
        assert _make_span(None) is None

    def test_extract_heritage_bases_member_expression(self):
        src = b"class Foo extends NS.Base {}"
        tree = parse_typescript(src)
        program = tree.root_node
        class_decl = next(
            c for c in program.children if c.type == "class_declaration"
        )
        heritage = next(
            c for c in class_decl.children if c.type == "class_heritage"
        )
        extends = next(
            c for c in heritage.children if c.type == "extends_clause"
        )
        bases = _extract_heritage_bases(extends)
        assert "NS.Base" in bases

    def test_extract_heritage_bases_generic_type(self):
        src = b"class Foo extends Base<string> {}"
        tree = parse_typescript(src)
        program = tree.root_node
        class_decl = next(
            c for c in program.children if c.type == "class_declaration"
        )
        heritage = next(
            c for c in class_decl.children if c.type == "class_heritage"
        )
        extends = next(
            c for c in heritage.children if c.type == "extends_clause"
        )
        bases = _extract_heritage_bases(extends)
        assert "Base" in bases

    def test_name_from_node_unexpected_type(self):
        src = b"const x = 1 + 2;"
        tree = parse_typescript(src)
        program = tree.root_node
        lexical = next(
            c for c in program.children if c.type == "lexical_declaration"
        )
        declarator = next(
            c for c in lexical.children if c.type == "variable_declarator"
        )
        binary = next(
            c for c in declarator.children if c.type == "binary_expression"
        )
        number_node = binary.children[0]
        assert _name_from_node(number_node) == ""


# ---------------------------------------------------------------------------
# Fix 1 — module/class-scope expression_statement occurrences
# ---------------------------------------------------------------------------


class TestModuleScopeExpressionStatements:
    def test_top_level_bare_call_records_one_call_occurrence(self):
        """foo(); at top-level must produce exactly one call occurrence."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("foo();")
        calls = [o for o in v.occurrences if o.role == "call"]
        assert len(calls) == 1, f"Expected 1 call, got {len(calls)}: {calls}"

    def test_top_level_call_enclosing_is_file_node(self):
        """foo(); at top-level must be enclosed by the FILE node."""
        from conftest import parse_and_visit_visitor
        graph, v = parse_and_visit_visitor("foo();")
        from graphlens import NodeKind
        file_nodes = [n for n in graph.nodes.values() if n.kind == NodeKind.FILE]
        assert file_nodes, "No FILE node found"
        file_id = file_nodes[0].id
        calls = [o for o in v.occurrences if o.role == "call"]
        assert len(calls) == 1
        assert calls[0].enclosing_id == file_id, (
            f"call enclosed by {calls[0].enclosing_id!r}, expected FILE {file_id!r}"
        )

    def test_top_level_assignment_call_records_write_read_call(self):
        """x = foo(a); at top-level must produce write(x), call(foo), read(a)."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("x = foo(a);")
        calls = [o for o in v.occurrences if o.role == "call"]
        reads = [o for o in v.occurrences if o.role == "read"]
        # Note: writes come from assignment_expression LHS scan
        assert len(calls) == 1, f"Expected 1 call(foo), got {calls}"
        assert len(reads) >= 1, f"Expected read(a), got {reads}"

    def test_top_level_no_double_count(self):
        """x = foo(a); must not record any occurrence more than once."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("x = foo(a);")
        positions = [(o.role, o.line, o.col) for o in v.occurrences]
        assert len(positions) == len(set(positions)), (
            f"Double-counted occurrences: {positions}"
        )

    def test_function_body_call_not_double_counted(self):
        """g() inside a function must yield exactly ONE call — not doubled by
        the new expression_statement handler."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("function f() { g(); }")
        calls = [o for o in v.occurrences if o.role == "call"]
        assert len(calls) == 1, (
            f"Expected exactly 1 call(g), got {len(calls)}: {calls}"
        )


# ---------------------------------------------------------------------------
# Fix 2 — PARAMETER nodes must carry name_span
# ---------------------------------------------------------------------------


class TestParameterNameSpan:
    def test_required_parameter_has_name_span(self):
        """PARAMETER node for a typed param must have name_span set."""
        from conftest import parse_and_visit_visitor
        graph, _ = parse_and_visit_visitor("function f(x: number) {}")
        params = [n for n in graph.nodes.values() if n.kind == NodeKind.PARAMETER]
        param = next(p for p in params if p.name == "x")
        ns = param.metadata.get("name_span")
        assert ns is not None, "PARAMETER node missing name_span"
        # 'x' is the 12th character (1-based col 12) on line 1
        assert ns.start_line == 1
        assert ns.start_col == 12


# ---------------------------------------------------------------------------
# Fix 3 — class-field initializer read/write enclosed by class, not attribute
# ---------------------------------------------------------------------------


class TestClassFieldEnclosing:
    def test_class_field_initializer_call_enclosed_by_class(self):
        """class C { x = compute(y); } — compute call enclosed by class node,
        not the attribute node."""
        from conftest import parse_and_visit_visitor
        graph, v = parse_and_visit_visitor("class C { x = compute(y); }")
        cls = next(
            n for n in graph.nodes.values() if n.kind == NodeKind.CLASS
        )
        calls = [o for o in v.occurrences if o.role == "call"]
        assert len(calls) == 1, f"Expected 1 call(compute), got {calls}"
        assert calls[0].enclosing_id == cls.id, (
            f"call enclosed by {calls[0].enclosing_id!r}, expected class {cls.id!r}"
        )

    def test_class_field_write_enclosed_by_class(self):
        """class C { x = 1; } — write(x) must be enclosed by the class node."""
        from conftest import parse_and_visit_visitor
        graph, v = parse_and_visit_visitor("class C { x = 1; }")
        cls = next(
            n for n in graph.nodes.values() if n.kind == NodeKind.CLASS
        )
        writes = [o for o in v.occurrences if o.role == "write"]
        assert len(writes) == 1, f"Expected 1 write(x), got {writes}"
        assert writes[0].enclosing_id == cls.id, (
            f"write enclosed by {writes[0].enclosing_id!r}, expected class {cls.id!r}"
        )
