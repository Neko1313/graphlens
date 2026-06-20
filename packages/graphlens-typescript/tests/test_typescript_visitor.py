"""Tests for TypeScript AST visitor."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from conftest import nodes_of_kind, parse_and_visit
from graphlens import GraphLens, Node, NodeKind, RelationKind
from graphlens.utils.ids import make_node_id

from graphlens_typescript._visitor import (
    ImportClassifier,
    TypescriptASTVisitor,
    VisitorContext,
    _extract_heritage_bases,
    _make_span,
    _name_from_node,
    _pkg_key_from_import_path,
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

    def test_scoped_package_default_import_third_party(self):
        # @scope/pkg listed as-is in third_party (normalized)
        classifier = ImportClassifier(
            third_party=frozenset({"@ant-design/icons"})
        )
        graph, _ = parse_and_visit(
            "import Icon from '@ant-design/icons';", classifier=classifier
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "Icon")
        assert imp.metadata.get("origin") == "third_party"

    def test_scoped_package_sub_path_third_party(self):
        # @scope/pkg/sub/path should resolve to @scope/pkg for lookup
        classifier = ImportClassifier(
            third_party=frozenset({"@ant-design/icons"})
        )
        graph, _ = parse_and_visit(
            "import { EyeOutlined } from '@ant-design/icons/lib/icons';",
            classifier=classifier,
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "EyeOutlined")
        assert imp.metadata.get("origin") == "third_party"

    def test_non_scoped_sub_path_third_party(self):
        # lodash/fp → key is "lodash"
        classifier = ImportClassifier(third_party=frozenset({"lodash"}))
        graph, _ = parse_and_visit(
            "import fp from 'lodash/fp';", classifier=classifier
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "fp")
        assert imp.metadata.get("origin") == "third_party"

    def test_at_alias_import_still_internal(self):
        # @/-style path-alias rewrites to a relative module — must stay internal
        # Simulate: @/utils/helpers is rewritten by path_aliases to src/utils/helpers
        # and classify_path ends up as utils/helpers (no @ prefix remains)
        graph, _ = parse_and_visit(
            "import { helper } from './utils/helpers';",
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "helper")
        assert imp.metadata.get("origin") == "internal"

    def test_relative_import_regression(self):
        # Regression: relative imports must always be internal regardless of classifier
        classifier = ImportClassifier(
            third_party=frozenset({"@scope/pkg"}),
            internal=frozenset({"local"}),
        )
        graph, _ = parse_and_visit(
            "import { x } from '../sibling';", classifier=classifier
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "x")
        assert imp.metadata.get("origin") == "internal"
        assert imp.metadata.get("is_relative") is True


class TestPkgKeyFromImportPath:
    """Unit tests for the _pkg_key_from_import_path helper."""

    def test_scoped_package_bare(self):
        assert _pkg_key_from_import_path("@ant-design/icons") == "@ant-design/icons"

    def test_scoped_package_with_sub_path(self):
        assert _pkg_key_from_import_path("@ant-design/icons/lib/foo") == "@ant-design/icons"

    def test_scoped_package_no_slash_after_at(self):
        # Malformed scoped import — treated as a single segment
        assert _pkg_key_from_import_path("@scope") == "@scope"

    def test_non_scoped_bare(self):
        assert _pkg_key_from_import_path("lodash") == "lodash"

    def test_non_scoped_with_sub_path(self):
        assert _pkg_key_from_import_path("lodash/fp") == "lodash"

    def test_stdlib_name(self):
        assert _pkg_key_from_import_path("fs") == "fs"

    def test_normalizes_hyphens_to_underscores(self):
        # normalize_pkg_name converts hyphens to underscores for non-scoped
        assert _pkg_key_from_import_path("some-pkg") == "some_pkg"

    def test_normalizes_scoped_to_lowercase(self):
        assert _pkg_key_from_import_path("@Scope/PKG") == "@scope/pkg"


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
        # exactly one call: "doSomething" starts at 1-based col 20
        assert len(calls) == 1
        assert calls[0].role == "call"
        assert calls[0].col == 20


class TestOccurrences:
    def test_call_records_call_occurrence(self):
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("function f() { a(); }")
        calls = [o for o in v.occurrences if o.role == "call"]
        # exactly one call: "a" starts at 1-based col 16
        assert len(calls) == 1
        assert calls[0].col == 16

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
        # exactly one read: "b" starts at 1-based col 18
        assert len(reads) == 1
        assert reads[0].col == 18

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
            f"write enclosed by {writes[0].enclosing_id!r}, "
            f"expected class {cls.id!r}"
        )


# ---------------------------------------------------------------------------
# Coverage: enum with initialised members (lines 384-403)
# ---------------------------------------------------------------------------


class TestEnumWithAssignments:
    def test_enum_assigned_members_are_attributes(self):
        """enum E { A = 1, B = 2 } — both members must be ATTRIBUTE nodes."""
        from conftest import parse_and_visit_visitor
        graph, _ = parse_and_visit_visitor("enum E { A = 1, B = 2 }")
        attrs = [
            n for n in graph.nodes.values() if n.kind == NodeKind.ATTRIBUTE
        ]
        names = {a.name for a in attrs}
        assert "A" in names
        assert "B" in names

    def test_enum_assigned_member_qualified_name(self):
        """Assigned enum member must have qualified_name enum.member."""
        from conftest import parse_and_visit_visitor
        graph, _ = parse_and_visit_visitor(
            "enum Dir { Up = 0, Down = 1 }", module_qname="myapp.mod"
        )
        attrs = [
            n for n in graph.nodes.values() if n.kind == NodeKind.ATTRIBUTE
        ]
        up = next(a for a in attrs if a.name == "Up")
        assert up.qualified_name == "myapp.mod.Dir.Up"

    def test_enum_mixed_bare_and_assigned(self):
        """Enum with both bare and assigned members produces all ATTRIBUTEs."""
        from conftest import parse_and_visit_visitor
        graph, _ = parse_and_visit_visitor("enum E { A, B = 1, C }")
        attrs = [
            n for n in graph.nodes.values() if n.kind == NodeKind.ATTRIBUTE
        ]
        names = {a.name for a in attrs}
        assert names == {"A", "B", "C"}


# ---------------------------------------------------------------------------
# Coverage: class field with type annotation (lines 423, 448)
# ---------------------------------------------------------------------------


class TestClassFieldTypeAnnotation:
    def test_field_with_type_annotation_only(self):
        """class C { x: MyType; } — ATTRIBUTE node created, annotation recorded."""
        from conftest import parse_and_visit_visitor
        graph, v = parse_and_visit_visitor("class C { x: MyType; }")
        attrs = [
            n for n in graph.nodes.values() if n.kind == NodeKind.ATTRIBUTE
        ]
        assert any(a.name == "x" for a in attrs)
        anns = [o for o in v.occurrences if o.role == "annotation"]
        assert len(anns) >= 1

    def test_field_with_type_annotation_write_occurrence(self):
        """class C { x: MyType; } — write occurrence on field name."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("class C { x: MyType; }")
        writes = [o for o in v.occurrences if o.role == "write"]
        assert len(writes) == 1

    def test_field_with_annotation_and_initializer(self):
        """class C { x: MyType = val; } — annotation + write + read."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("class C { x: MyType = foo; }")
        anns = [o for o in v.occurrences if o.role == "annotation"]
        writes = [o for o in v.occurrences if o.role == "write"]
        reads = [o for o in v.occurrences if o.role == "read"]
        assert len(anns) >= 1
        assert len(writes) == 1
        assert len(reads) >= 1


# ---------------------------------------------------------------------------
# Coverage: type alias annotation recording (line 479)
# ---------------------------------------------------------------------------


class TestTypeAliasAnnotation:
    def test_type_alias_annotation_occurrence_recorded(self):
        """type A = SomeType; — annotation occurrence on SomeType."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("type A = SomeType;")
        anns = [o for o in v.occurrences if o.role == "annotation"]
        assert len(anns) >= 1

    def test_type_alias_predefined_annotation(self):
        """type A = string; — annotation occurrence on predefined type."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("type A = string;")
        anns = [o for o in v.occurrences if o.role == "annotation"]
        assert len(anns) >= 1

    def test_type_alias_generic_annotation(self):
        """type A = Array<string>; — annotation on generic type base."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("type A = Array<string>;")
        anns = [o for o in v.occurrences if o.role == "annotation"]
        assert len(anns) >= 1

    def test_type_alias_member_expr_annotation(self):
        """type A = typeof obj.prop; — _first_identifier handles member_expression."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("type A = typeof obj.prop;")
        # member_expression branch returns the trailing property node ("prop")
        # so an annotation occurrence is recorded for it
        anns = [o for o in v.occurrences if o.role == "annotation"]
        assert len(anns) >= 1

    def test_type_alias_object_type_hits_line_1146(self):
        """type A = { x: number }; — _first_identifier recurses through object_type."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("type A = { x: number };")
        # Recursive fallback descends into the object_type and eventually
        # finds the predefined_type child "number", recording an annotation
        anns = [o for o in v.occurrences if o.role == "annotation"]
        assert len(anns) >= 1


# ---------------------------------------------------------------------------
# Coverage: _handle_class / _visit_interface_declaration early return (lines
# 479, 530) — triggered when the node has no recognisable name child
# ---------------------------------------------------------------------------


class TestAnonymousNamelessNodes:
    def test_interface_without_name_skipped(self):
        """Malformed / anonymous interface node with no type_identifier is skipped."""
        # The TS grammar wraps unnamed interfaces differently; we exercise the
        # guard by directly calling _visit_interface_declaration with a stub.
        from conftest import parse_and_visit_visitor
        # The parser won't normally produce this but we test the guard exists
        # by using a class_body expression_statement path (safe graceful skip)
        graph, _ = parse_and_visit_visitor("interface {}")
        # The nameless interface must produce no CLASS node
        classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
        assert len(classes) == 0

    def test_handle_class_no_name_skipped(self):
        """Anonymous class expression does not crash the visitor."""
        graph, _ = parse_and_visit("export default class {}")
        # The nameless class must produce no CLASS node
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert len(classes) == 0


# ---------------------------------------------------------------------------
# Coverage: lexical_declaration with destructuring (line 669)
# ---------------------------------------------------------------------------


class TestLexicalDestructuring:
    def test_object_destructuring_literal_rhs_skipped(self):
        """const {a} = {} — RHS object literal means no identifier child found."""
        # This triggers line 669 (name_node is None -> continue)
        graph, _ = parse_and_visit("const {a} = {};")
        # No VARIABLE node for 'a' since the declarator has no identifier child
        variables = nodes_of_kind(graph, NodeKind.VARIABLE)
        assert not any(v.name == "a" for v in variables)

    def test_array_destructuring_literal_rhs_skipped(self):
        """const [a] = [] — RHS array literal means no identifier child found."""
        graph, _ = parse_and_visit("const [a] = [];")
        variables = nodes_of_kind(graph, NodeKind.VARIABLE)
        assert not any(v.name == "a" for v in variables)

    def test_object_destructuring_identifier_rhs(self):
        """const {a, b} = obj — RHS is identifier; name_node='obj' is found."""
        # This does NOT trigger line 669 because 'obj' is an identifier child
        graph, _ = parse_and_visit("const {a, b} = obj;")
        assert graph is not None


# ---------------------------------------------------------------------------
# Coverage: import module resolution hit (lines 956-957)
# ---------------------------------------------------------------------------


def _make_visitor_with_modules(  # noqa: PLR0913
    source: str,
    modules: dict,
    module_qname: str = "myapp.mod",
    project_name: str = "myapp",
    classifier=None,
    pre_add_nodes=None,
):
    """Build a visitor that has a pre-populated modules mapping.

    ``modules`` maps qualified_name -> node_id.
    ``pre_add_nodes`` is an optional list of Node objects to add to the graph
    before visiting (so that RESOLVES_TO targets are found in the graph).
    """
    source_bytes = source.encode("utf-8")
    tree = parse_typescript(source_bytes)
    graph = GraphLens()
    rel_path = "src/myapp/mod.ts"
    file_id = make_node_id(project_name, rel_path, NodeKind.FILE.value)
    file_node = Node(
        id=file_id,
        kind=NodeKind.FILE,
        qualified_name=rel_path,
        name="mod.ts",
        file_path=rel_path,
    )
    graph.add_node(file_node)
    for n in (pre_add_nodes or []):
        graph.add_node(n)
    abs_path = Path("/").resolve() / rel_path
    ctx = VisitorContext(
        project_name=project_name,
        file_path=abs_path,
        file_relative_path=rel_path,
        source_root=Path("src"),
        module_qualified_name=module_qname,
        modules=modules,
    )
    visitor = TypescriptASTVisitor(
        ctx, graph, file_id, source_bytes, classifier
    )
    visitor.visit(tree.root_node)
    return graph, visitor, file_id


class TestInternalModuleResolution:
    def test_relative_import_resolves_to_existing_module(self):
        """import { foo } from './utils' — RESOLVES_TO the utils MODULE node."""
        project_name = "myapp"
        utils_qname = "myapp.utils"
        utils_id = make_node_id(
            project_name, utils_qname, NodeKind.MODULE.value
        )
        utils_node = Node(
            id=utils_id,
            kind=NodeKind.MODULE,
            qualified_name=utils_qname,
            name="utils",
        )
        modules = {utils_qname: utils_id}

        graph, _, _ = _make_visitor_with_modules(
            "import { foo } from './utils';",
            modules=modules,
            pre_add_nodes=[utils_node],
        )
        # The IMPORT node must RESOLVES_TO the utils module, not an EXTERNAL_SYMBOL
        resolves = [
            r for r in graph.relations
            if r.kind == RelationKind.RESOLVES_TO
        ]
        assert len(resolves) >= 1
        target = graph.nodes.get(resolves[0].target_id)
        assert target is not None
        assert target.kind == NodeKind.MODULE
        assert target.qualified_name == utils_qname

    def test_relative_import_no_module_match_creates_external_symbol(self):
        """import from './missing' with no matching module creates EXTERNAL_SYMBOL."""
        graph, _, _ = _make_visitor_with_modules(
            "import { bar } from './missing';",
            modules={},
        )
        ext_syms = [
            n for n in graph.nodes.values()
            if n.kind == NodeKind.EXTERNAL_SYMBOL
        ]
        assert len(ext_syms) >= 1


# ---------------------------------------------------------------------------
# Coverage: _walk_statement augmented_assignment (lines 1305-1306)
# ---------------------------------------------------------------------------


class TestWalkStatementEdgeCases:
    def test_augmented_assignment_in_function_body(self):
        """x += 1 inside a function body produces no call occurrence."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("function f() { x += 1; }")
        calls = [o for o in v.occurrences if o.role == "call"]
        assert len(calls) == 0

    def test_if_statement_in_function_body(self):
        """if/else inside a function body – calls in both branches recorded."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor(
            "function f() { if (c) { g(); } else { h(); } }"
        )
        calls = [o for o in v.occurrences if o.role == "call"]
        {o.col for o in calls}
        assert len(calls) >= 2

    def test_for_statement_in_function_body(self):
        """for loop body calls are recorded."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor(
            "function f() { for (let i = 0; i < 10; i++) { g(i); } }"
        )
        calls = [o for o in v.occurrences if o.role == "call"]
        assert len(calls) >= 1

    def test_while_statement_in_function_body(self):
        """while loop body calls are recorded."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor(
            "function f() { while (cond) { g(); } }"
        )
        calls = [o for o in v.occurrences if o.role == "call"]
        assert len(calls) >= 1

    def test_throw_statement_triggers_fallthrough(self):
        """throw new Error() hits the _scan_value fallthrough branch."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor(
            "function f() { throw new Error('oops'); }"
        )
        # 'Error' should be recorded as a read occurrence via _scan_value
        reads = [o for o in v.occurrences if o.role == "read"]
        assert len(reads) >= 1

    def test_try_catch_in_function_body(self):
        """try/catch block: calls inside are recorded."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor(
            "function f() { try { g(); } catch (e) { h(); } }"
        )
        calls = [o for o in v.occurrences if o.role == "call"]
        assert len(calls) >= 2

    def test_switch_in_function_body(self):
        """switch/case: calls in branches are recorded."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor(
            "function f(x) { switch (x) { case 1: g(); break; default: h(); } }"
        )
        calls = [o for o in v.occurrences if o.role == "call"]
        assert len(calls) >= 2


# ---------------------------------------------------------------------------
# Coverage: _scan_value – pair and shorthand_property_identifier
# (lines 1245-1249, 1253-1254)
# ---------------------------------------------------------------------------


class TestScanValueObjectLiterals:
    def test_pair_value_scanned(self):
        """return { key: val } — val is scanned as read."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor(
            "function f() { return { key: val }; }"
        )
        reads = [o for o in v.occurrences if o.role == "read"]
        assert len(reads) >= 1

    def test_shorthand_property_scanned(self):
        """return { x } — x is recorded as read via shorthand."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("function f() { return { x }; }")
        reads = [o for o in v.occurrences if o.role == "read"]
        assert len(reads) >= 1

    def test_pair_key_not_scanned(self):
        """Object literal key (not a value identifier) is not double-counted."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor(
            "function f() { return { key: myVar }; }"
        )
        {o.col for o in v.occurrences if o.role == "read"}
        # Only 'myVar' should be read, not 'key'
        reads = [o for o in v.occurrences if o.role == "read"]
        assert len(reads) == 1


# ---------------------------------------------------------------------------
# Coverage: _scan_value – _NESTED_DEF_TYPES guard (line 1214)
# ---------------------------------------------------------------------------


class TestScanValueNestedDefGuard:
    def test_arrow_in_object_value_not_traversed(self):
        """{ fn: () => x } — arrow inside pair value is a scope boundary."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor(
            "function f() { return { fn: () => outer }; }"
        )
        # 'outer' inside the arrow is in a new scope; scan_value should stop
        # The arrow_function is in NESTED_DEF_TYPES; no 'read' for outer
        reads = [o for o in v.occurrences if o.role == "read"]
        assert len(reads) == 0


# ---------------------------------------------------------------------------
# Coverage: _first_identifier – various type node branches
# Lines 1120, 1127, 1130, 1139, 1146
# ---------------------------------------------------------------------------


class TestFirstIdentifier:
    def test_generic_type_returns_base_name(self):
        """type A = Array<string> — annotation occurrence on 'Array'."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("type A = Array<string>;")
        anns = [o for o in v.occurrences if o.role == "annotation"]
        assert len(anns) >= 1

    def test_predefined_type_annotation_recorded(self):
        """function f(x: string) — annotation occurrence on predefined 'string'."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("function f(x: string): void {}")
        anns = [o for o in v.occurrences if o.role == "annotation"]
        assert len(anns) >= 1

    def test_member_expression_in_type_position(self):
        """type A = typeof obj.prop — member_expression branch in _first_identifier."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("type A = typeof obj.prop;")
        # member_expression returns its trailing child → annotation is recorded
        anns = [o for o in v.occurrences if o.role == "annotation"]
        assert len(anns) >= 1

    def test_object_type_recursive_fallback(self):
        """type A = { x: number } — recursive fallback in _first_identifier."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("type A = { x: number };")
        # Recursive descent finds the predefined_type "number" and records it
        anns = [o for o in v.occurrences if o.role == "annotation"]
        assert len(anns) >= 1


# ---------------------------------------------------------------------------
# Coverage: _make_span exception path (lines 1546-1547)
# ---------------------------------------------------------------------------


class TestMakeSpan:
    def test_make_span_exception_returns_none(self):
        """_make_span catches any exception from a bad node and returns None."""
        bad_node = MagicMock()
        bad_node.start_point = MagicMock(side_effect=ValueError("bad"))
        result = _make_span(bad_node)
        assert result is None


# ---------------------------------------------------------------------------
# Coverage: _string_from_from_clause early return (lines 1440-1445)
# ---------------------------------------------------------------------------


class TestStringFromFromClause:
    def test_from_clause_without_string_returns_empty(self):
        """_string_from_from_clause returns '' when no string child is found."""
        from graphlens_typescript._visitor import _string_from_from_clause
        # Build a mock from_clause with no 'string' child
        mock_clause = MagicMock()
        mock_clause.children = []  # no string child
        result = _string_from_from_clause(mock_clause)
        assert result == ""


# ---------------------------------------------------------------------------
# Coverage: import_statement with empty module_path guard (line 828)
# ---------------------------------------------------------------------------


class TestImportEmptyModulePath:
    def test_import_from_clause_with_no_string_is_skipped(self):
        """import_statement where module_path resolves to '' is skipped."""
        # Inject a visitor with a synthetically empty from_clause by
        # calling _visit_import_statement indirectly via malformed source.
        # The TS parser with error recovery may produce import nodes without
        # a proper module path; the visitor must not crash.
        from conftest import parse_and_visit_visitor
        # 'import {}' without a 'from' — parser creates an ERROR node
        graph, _ = parse_and_visit_visitor("import {};")
        assert graph is not None


# ---------------------------------------------------------------------------
# Coverage: export abstract class (line 307)
# ---------------------------------------------------------------------------


class TestExportAbstractClass:
    def test_export_abstract_class(self):
        """export abstract class Foo {} — abstract class node created."""
        graph, _ = parse_and_visit("export abstract class Foo {}")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        foo = next((c for c in classes if c.name == "Foo"), None)
        assert foo is not None
        assert foo.metadata.get("is_abstract") is True


# ---------------------------------------------------------------------------
# Coverage: _visit_expression_statement assignment at top-level (line 204)
# ---------------------------------------------------------------------------


class TestExpressionStatementEdgeCases:
    def test_top_level_augmented_assignment_rhs_scanned(self):
        """x += foo(y) at top-level — foo call and y read are recorded."""
        from conftest import parse_and_visit_visitor
        _, v = parse_and_visit_visitor("x += foo(y);")
        calls = [o for o in v.occurrences if o.role == "call"]
        reads = [o for o in v.occurrences if o.role == "read"]
        assert len(calls) >= 1
        assert len(reads) >= 1


# ---------------------------------------------------------------------------
# Coverage: this parameter skipped (line 347, 1076-1077)
# ---------------------------------------------------------------------------


class TestThisParameter:
    def test_this_parameter_is_skipped(self):
        """function f(this: MyClass, x: number) — 'this' param not in graph."""
        graph, _ = parse_and_visit(
            "function f(this: MyClass, x: number) {}"
        )
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        assert not any(p.name == "this" for p in params)
        assert any(p.name == "x" for p in params)


# ---------------------------------------------------------------------------
# Coverage: required_parameter rest_pattern path (lines 200-204 in visitor,
# also covers rest param recording via required_parameter+rest_pattern)
# ---------------------------------------------------------------------------


class TestRequiredParameterRestPattern:
    def test_rest_param_in_required_parameter(self):
        """function f(...args: string[]) — rest param via required_parameter."""
        graph, _ = parse_and_visit("function f(...args: string[]) {}")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        assert any(p.name == "args" for p in params)
        param = next(p for p in params if p.name == "args")
        assert param.metadata.get("is_variadic") is True


# ---------------------------------------------------------------------------
# Coverage: computed enum key skips (line 392)
# ---------------------------------------------------------------------------


class TestEnumComputedKey:
    def test_computed_enum_assignment_skipped(self):
        """const enum E { [A] = 1 } — computed key has no property_identifier."""
        graph, _ = parse_and_visit("const enum E { [A] = 1 }")
        # The enum E itself is created (CLASS node)
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "E" for c in classes)
        # But the computed member [A] has no property_identifier → skipped
        attrs = nodes_of_kind(graph, NodeKind.ATTRIBUTE)
        assert len(attrs) == 0

    def test_string_key_enum_assignment_skipped(self):
        """enum E { \"A\" = 1 } — string key has no property_identifier."""
        graph, _ = parse_and_visit('enum E { "A" = 1 }')
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "E" for c in classes)
        attrs = nodes_of_kind(graph, NodeKind.ATTRIBUTE)
        assert len(attrs) == 0


# ---------------------------------------------------------------------------
# Coverage: class field without property_identifier (line 423)
# ---------------------------------------------------------------------------


class TestClassFieldPrivateOrComputed:
    def test_private_field_skipped(self):
        """class C { #x = 1; } — private field has no property_identifier."""
        graph, _ = parse_and_visit("class C { #x = 1; }")
        # Class node must exist
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "C" for c in classes)
        # Private field has private_property_identifier, not property_identifier
        # so _visit_public_field_definition returns early at line 423
        attrs = nodes_of_kind(graph, NodeKind.ATTRIBUTE)
        assert not any(a.name == "#x" for a in attrs)

    def test_computed_field_skipped(self):
        """class C { [key] = 1; } — computed field has no property_identifier."""
        graph, _ = parse_and_visit('class C { ["key"] = 1; }')
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert any(c.name == "C" for c in classes)
        attrs = nodes_of_kind(graph, NodeKind.ATTRIBUTE)
        assert len(attrs) == 0


# ---------------------------------------------------------------------------
# Coverage: handle_class no-name guard (line 479)
# These fire when the internal _handle_class / _visit_interface_declaration
# receive a node without any recognisable name token.  We reach them via
# direct method calls with a mock node rather than relying on the grammar
# producing such a construct.
# ---------------------------------------------------------------------------


def _make_simple_visitor(project_name="myapp"):
    """Create a minimal TypescriptASTVisitor ready for direct method calls."""
    rel_path = "src/myapp/mod.ts"
    file_id = make_node_id(project_name, rel_path, NodeKind.FILE.value)
    graph = GraphLens()
    graph.add_node(
        Node(
            id=file_id,
            kind=NodeKind.FILE,
            qualified_name=rel_path,
            name="mod.ts",
            file_path=rel_path,
        )
    )
    abs_path = Path("/").resolve() / rel_path
    ctx = VisitorContext(
        project_name=project_name,
        file_path=abs_path,
        file_relative_path=rel_path,
        source_root=Path("src"),
        module_qualified_name="myapp.mod",
    )
    v = TypescriptASTVisitor(ctx, graph, file_id, b"", None)
    return v, graph


class TestInternalGuards:
    def test_handle_class_no_name_returns_early(self):
        """_handle_class with a node that has no name child is a no-op."""
        v, graph = _make_simple_visitor()
        node = MagicMock()
        node.children = []  # no type_identifier, no identifier
        initial_nodes = len(graph.nodes)
        v._handle_class(node, decorators=[], is_abstract=False)
        assert len(graph.nodes) == initial_nodes

    def test_visit_interface_no_name_returns_early(self):
        """_visit_interface_declaration with no name child is a no-op."""
        v, graph = _make_simple_visitor()
        node = MagicMock()
        node.children = []
        initial_nodes = len(graph.nodes)
        v._visit_interface_declaration(node)
        assert len(graph.nodes) == initial_nodes

    def test_visit_type_alias_no_name_returns_early(self):
        """_visit_type_alias_declaration with no type_identifier is a no-op."""
        v, graph = _make_simple_visitor()
        node = MagicMock()
        node.children = []
        initial_nodes = len(graph.nodes)
        v._visit_type_alias_declaration(node)
        assert len(graph.nodes) == initial_nodes

    def test_visit_enum_no_name_returns_early(self):
        """_visit_enum_declaration with no identifier is a no-op."""
        v, graph = _make_simple_visitor()
        node = MagicMock()
        node.children = []
        initial_nodes = len(graph.nodes)
        v._visit_enum_declaration(node)
        assert len(graph.nodes) == initial_nodes

    def test_handle_function_no_name_returns_early(self):
        """_handle_function with no name child in _METHOD_NAME_TYPES is no-op."""
        # Computed method: [Symbol.iterator]() {} has computed_property_name
        graph, _ = parse_and_visit("class C { [Symbol.iterator]() {} }")
        methods = nodes_of_kind(graph, NodeKind.METHOD)
        # The computed method is skipped (no property_identifier/identifier)
        assert len(methods) == 0

    def test_record_occurrence_sp_none_returns_early(self):
        """_record_occurrence with a node whose _make_span fails is a no-op."""
        v, _ = _make_simple_visitor()
        bad_node = MagicMock()
        bad_node.start_point = MagicMock(side_effect=RuntimeError("bad"))
        initial_count = len(v.occurrences)
        v._record_occurrence("read", bad_node, "some-id")
        assert len(v.occurrences) == initial_count

    def test_process_named_imports_empty_identifiers_skipped(self):
        """import_specifier with no identifier children is skipped."""
        v, graph = _make_simple_visitor()
        # Build a mock named_imports node with a specifier that has no identifiers
        spec = MagicMock()
        spec.type = "import_specifier"
        spec.children = []  # no identifier children
        named_imports = MagicMock()
        named_imports.children = [spec]
        initial_nodes = len(graph.nodes)
        v._process_named_imports(
            named_imports, ext_qname="mod", is_relative=False
        )
        assert len(graph.nodes) == initial_nodes

    def test_visit_expression_statement_function_scope_skips(self):
        """_visit_expression_statement called while FUNCTION is on kind_stack."""
        v, _ = _make_simple_visitor()
        from graphlens import NodeKind as NK
        # Push FUNCTION onto kind_stack to simulate being inside a function body
        v._kind_stack.append(NK.FUNCTION)
        # Call with a mock expression_statement node
        node = MagicMock()
        node.children = []
        initial_count = len(v.occurrences)
        v._visit_expression_statement(node)
        assert len(v.occurrences) == initial_count
        v._kind_stack.pop()

    def test_visit_expression_statement_no_expr_child(self):
        """_visit_expression_statement with no named child is a no-op."""
        v, _ = _make_simple_visitor()
        # kind_stack[-1] is FILE (default), so not skipped by FUNCTION guard
        node = MagicMock()
        # Return no named children
        node.children = []
        initial_count = len(v.occurrences)
        v._visit_expression_statement(node)
        assert len(v.occurrences) == initial_count


# ---------------------------------------------------------------------------
# Coverage: _string_from_from_clause early return (lines 1440-1445)
# ---------------------------------------------------------------------------


class TestStringFromFromClauseDead:
    def test_from_clause_with_string_returns_path(self):
        """_string_from_from_clause returns the unquoted path when string found."""
        from graphlens_typescript._visitor import _string_from_from_clause
        # Build a mock from_clause that has a 'string' child
        str_child = MagicMock()
        str_child.type = "string"
        str_child.text = b"'./utils'"
        mock_clause = MagicMock()
        mock_clause.children = [str_child]
        result = _string_from_from_clause(mock_clause)
        assert result == "./utils"


# ---------------------------------------------------------------------------
# Coverage: import_statement with empty module_path (line 828)
# Coverage: _visit_import_statement with from_clause that has no string child
# (line 817 + line 828 via direct call)
# ---------------------------------------------------------------------------


class TestImportStatementGuards:
    def test_import_statement_no_module_path_skipped(self):
        """import_statement where module_path is '' is gracefully skipped."""
        v, graph = _make_simple_visitor()
        # Build a mock import_statement with a from_clause but no string child
        from_clause = MagicMock()
        from_clause.type = "from_clause"
        from_clause.children = []  # no string child -> _string_from_from_clause returns ''
        node = MagicMock()
        node.children = [from_clause]
        initial_nodes = len(graph.nodes)
        v._visit_import_statement(node)
        assert len(graph.nodes) == initial_nodes

    def test_import_statement_side_effect_no_string_skipped(self):
        """import_statement else branch with no string child is skipped."""
        v, graph = _make_simple_visitor()
        # No from_clause and no string child -> str_node is None -> module_path=''
        node = MagicMock()
        node.children = []  # no from_clause, no string
        initial_nodes = len(graph.nodes)
        v._visit_import_statement(node)
        assert len(graph.nodes) == initial_nodes


# ---------------------------------------------------------------------------
# Coverage: lexical_declaration with destructuring (already covers line 669)
# Plus: variable_declarator with no identifier (destructuring)
# ---------------------------------------------------------------------------


class TestLexicalDeclarationDestructuring:
    def test_object_destructuring_no_crash(self):
        """const { a } = obj at top level does not crash."""
        graph, _ = parse_and_visit("const { a } = obj;")
        assert graph is not None

    def test_array_destructuring_no_crash(self):
        """const [a] = arr at top level does not crash."""
        graph, _ = parse_and_visit("const [a] = arr;")
        assert graph is not None


# ---------------------------------------------------------------------------
# Coverage: dead-code guards that are forward-compatible safety nets.
# The current tree-sitter-typescript grammar (v0.23.2) never produces:
#   - bare 'identifier' as a direct formal_parameters child  (lines 996-997)
#   - 'rest_parameter' node type  (lines 1060-1065)
#   - 'assignment_pattern' as a direct formal_parameters child (lines 1069-1074)
#   - 'type_annotation' with no named children  (line 1120)
#   - 'predefined_type' with no children  (line 1127)
# We exercise these paths via direct _extract_parameters and _first_identifier
# calls with mock nodes.
# ---------------------------------------------------------------------------


class TestDeadCodeSafetyNets:
    def test_extract_params_bare_identifier(self):
        """_extract_parameters handles a bare 'identifier' formal param child."""
        v, graph = _make_simple_visitor()
        # Simulate a grammar version where bare identifiers appear in params
        id_node = MagicMock()
        id_node.type = "identifier"
        id_node.is_named = True
        id_node.text = b"x"
        id_node.children = []
        id_node.start_point = (0, 0)
        id_node.end_point = (0, 1)

        params_node = MagicMock()
        params_node.children = [id_node]

        v._extract_parameters(params_node, "fn-id", "myapp.mod.f")
        params = [
            n for n in graph.nodes.values() if n.kind == NodeKind.PARAMETER
        ]
        assert any(p.name == "x" for p in params)

    def test_extract_params_rest_parameter_node(self):
        """_extract_parameters handles an explicit 'rest_parameter' node."""
        v, graph = _make_simple_visitor()
        id_node = MagicMock()
        id_node.type = "identifier"
        id_node.is_named = True
        id_node.text = b"args"
        id_node.children = []
        id_node.start_point = (0, 3)
        id_node.end_point = (0, 7)

        rest_node = MagicMock()
        rest_node.type = "rest_parameter"
        rest_node.is_named = True
        rest_node.children = [id_node]
        rest_node.text = b"...args"
        rest_node.start_point = (0, 0)
        rest_node.end_point = (0, 7)

        params_node = MagicMock()
        params_node.children = [rest_node]

        v._extract_parameters(params_node, "fn-id", "myapp.mod.f")
        params = [
            n for n in graph.nodes.values() if n.kind == NodeKind.PARAMETER
        ]
        assert any(p.name == "args" for p in params)
        param = next(p for p in params if p.name == "args")
        assert param.metadata.get("is_variadic") is True

    def test_extract_params_assignment_pattern_node(self):
        """_extract_parameters handles an explicit 'assignment_pattern' node."""
        v, graph = _make_simple_visitor()
        id_node = MagicMock()
        id_node.type = "identifier"
        id_node.is_named = True
        id_node.text = b"x"
        id_node.children = []
        id_node.start_point = (0, 0)
        id_node.end_point = (0, 1)

        ap_node = MagicMock()
        ap_node.type = "assignment_pattern"
        ap_node.is_named = True
        ap_node.children = [id_node]
        ap_node.text = b"x = 5"
        ap_node.start_point = (0, 0)
        ap_node.end_point = (0, 5)

        params_node = MagicMock()
        params_node.children = [ap_node]

        v._extract_parameters(params_node, "fn-id", "myapp.mod.f")
        params = [
            n for n in graph.nodes.values() if n.kind == NodeKind.PARAMETER
        ]
        assert any(p.name == "x" for p in params)
        param = next(p for p in params if p.name == "x")
        assert param.metadata.get("has_default") is True

    def test_first_identifier_type_annotation_no_named_children(self):
        """_first_identifier returns None for type_annotation with no named child."""
        v, _ = _make_simple_visitor()
        node = MagicMock()
        node.type = "type_annotation"
        node.children = []  # no named children
        result = v._first_identifier(node)
        assert result is None

    def test_first_identifier_predefined_type_no_children(self):
        """_first_identifier returns None for predefined_type with no children."""
        v, _ = _make_simple_visitor()
        node = MagicMock()
        node.type = "predefined_type"
        node.children = []  # no children (impossible in real grammar)
        result = v._first_identifier(node)
        assert result is None
