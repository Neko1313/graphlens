"""Tests for TypeScript AST visitor."""

from __future__ import annotations

from conftest import nodes_of_kind, parse_and_visit
from graphlens import NodeKind, RelationKind

from graphlens_typescript._visitor import ImportClassifier


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

    def test_declares_relation_for_params(self):
        graph, _ = parse_and_visit("function f(x: number, y: string) {}")
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        func = next(f for f in funcs if f.name == "f")
        declares = [
            r for r in graph.relations
            if r.kind == RelationKind.DECLARES and r.source_id == func.id
        ]
        assert len(declares) == 2


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
