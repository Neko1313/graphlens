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
        graph, _ = parse_and_visit("class Child extends Parent {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        child = next(c for c in classes if c.name == "Child")
        inherits = [
            r for r in graph.relations
            if r.kind == RelationKind.INHERITS_FROM and r.source_id == child.id
        ]
        assert len(inherits) == 1


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
        graph, _ = parse_and_visit("function greet(name: string): string { return name; }")
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
        graph, _ = parse_and_visit("import fs from 'fs';", classifier=classifier)
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
        graph, _ = parse_and_visit("import fs from 'fs';", classifier=classifier)
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        fs_import = next(i for i in imports if i.name == "fs")
        assert fs_import.metadata.get("origin") == "stdlib"

    def test_third_party_origin(self):
        classifier = ImportClassifier(third_party=frozenset({"lodash"}))
        graph, _ = parse_and_visit("import _ from 'lodash';", classifier=classifier)
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
            r for r in graph.relations
            if r.kind == RelationKind.IMPORTS and r.source_id == file_id
        ]
        assert len(imports_rels) >= 1

    def test_resolves_to_relation(self):
        graph, _ = parse_and_visit(
            "import { foo } from 'somemod';",
            classifier=ImportClassifier(third_party=frozenset({"somemod"})),
        )
        resolves = [r for r in graph.relations if r.kind == RelationKind.RESOLVES_TO]
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
        src = "function greet() { console.log('hi'); }"
        graph, _ = parse_and_visit(src)
        calls = [r for r in graph.relations if r.kind == RelationKind.CALLS]
        assert len(calls) >= 1

    def test_call_creates_symbol_node(self):
        src = "function greet() { doSomething(); }"
        graph, _ = parse_and_visit(src)
        symbols = nodes_of_kind(graph, NodeKind.SYMBOL)
        assert any(s.name == "doSomething" for s in symbols)


class TestArrowFunction:
    def test_const_arrow_function(self):
        graph, _ = parse_and_visit("export const greet = (name: string) => name;")
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
        c = ImportClassifier(stdlib=frozenset({"mod"}), internal=frozenset({"mod"}))
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
            r for r in graph.relations
            if r.kind == RelationKind.DECLARES and r.source_id == func.id
        ]
        assert len(declares) == 2


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
        class_decl = next(c for c in program.children if c.type == "class_declaration")
        heritage = next(c for c in class_decl.children if c.type == "class_heritage")
        extends = next(c for c in heritage.children if c.type == "extends_clause")
        bases = _extract_heritage_bases(extends)
        assert "NS.Base" in bases

    def test_extract_heritage_bases_generic_type(self):
        src = b"class Foo extends Base<string> {}"
        tree = parse_typescript(src)
        program = tree.root_node
        class_decl = next(c for c in program.children if c.type == "class_declaration")
        heritage = next(c for c in class_decl.children if c.type == "class_heritage")
        extends = next(c for c in heritage.children if c.type == "extends_clause")
        bases = _extract_heritage_bases(extends)
        assert "Base" in bases

    def test_name_from_node_unexpected_type(self):
        src = b"const x = 1 + 2;"
        tree = parse_typescript(src)
        program = tree.root_node
        lexical = next(c for c in program.children if c.type == "lexical_declaration")
        declarator = next(
            c for c in lexical.children if c.type == "variable_declarator"
        )
        binary = next(c for c in declarator.children if c.type == "binary_expression")
        number_node = binary.children[0]
        assert _name_from_node(number_node) == ""
