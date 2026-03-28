"""Tests for PythonASTVisitor."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

from code_graph import CodeGraph, NodeKind, RelationKind
from code_graph.utils.ids import make_node_id
from conftest import nodes_of_kind, parse_and_visit

from code_graph_python._visitor import (
    ImportClassifier,
    PythonASTVisitor,
    VisitorContext,
    _decorator_name,
    _find_module_node_id,
    _make_span,
    _name_from_node,
    parse_python,
)

# ---------------------------------------------------------------------------
# ImportClassifier
# ---------------------------------------------------------------------------


class TestImportClassifier:
    def setup_method(self):
        self.classifier = ImportClassifier(
            stdlib=frozenset({"os", "sys", "json"}),
            third_party=frozenset({"requests", "flask"}),
            internal=frozenset({"mypkg", "mylib"}),
        )

    def test_classify_stdlib(self):
        assert self.classifier.classify("os") == "stdlib"
        assert self.classifier.classify("sys") == "stdlib"

    def test_classify_internal(self):
        assert self.classifier.classify("mypkg") == "internal"

    def test_classify_third_party(self):
        assert self.classifier.classify("requests") == "third_party"

    def test_classify_unknown(self):
        assert self.classifier.classify("some_random_pkg") == "unknown"

    def test_default_classifier_classifies_unknown(self):
        c = ImportClassifier()
        assert c.classify("anything") == "unknown"


# ---------------------------------------------------------------------------
# Class extraction
# ---------------------------------------------------------------------------


class TestClassExtraction:
    def test_simple_class(self):
        graph, _ = parse_and_visit("class Foo:\n    pass\n")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert len(classes) == 1
        assert classes[0].name == "Foo"

    def test_class_qualified_name(self):
        graph, _ = parse_and_visit("class Bar:\n    pass\n", module_qname="mypkg.mod")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        assert classes[0].qualified_name == "mypkg.mod.Bar"

    def test_class_with_base(self):
        graph, _ = parse_and_visit("class Child(Base):\n    pass\n")
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        cls = classes[0]
        assert "Base" in cls.metadata["bases"]

    def test_class_with_multiple_bases(self):
        graph, _ = parse_and_visit("class C(A, B):\n    pass\n")
        cls = nodes_of_kind(graph, NodeKind.CLASS)[0]
        assert "A" in cls.metadata["bases"]
        assert "B" in cls.metadata["bases"]

    def test_abstract_class_detected(self):
        graph, _ = parse_and_visit("class MyABC(ABC):\n    pass\n")
        cls = nodes_of_kind(graph, NodeKind.CLASS)[0]
        assert cls.metadata["is_abstract"] is True

    def test_non_abstract_class(self):
        graph, _ = parse_and_visit("class Foo:\n    pass\n")
        cls = nodes_of_kind(graph, NodeKind.CLASS)[0]
        assert cls.metadata["is_abstract"] is False

    def test_class_has_span(self):
        graph, _ = parse_and_visit("class Foo:\n    pass\n")
        cls = nodes_of_kind(graph, NodeKind.CLASS)[0]
        assert cls.span is not None
        assert cls.span.start_line >= 1

    def test_class_declares_relation(self):
        graph, file_id = parse_and_visit("class Foo:\n    pass\n")
        cls = nodes_of_kind(graph, NodeKind.CLASS)[0]
        declares = [
            r for r in graph.relations
            if r.source_id == file_id and r.target_id == cls.id
            and r.kind == RelationKind.DECLARES
        ]
        assert len(declares) == 1

    def test_class_inherits_from_relation(self):
        graph, _ = parse_and_visit("class Child(Base):\n    pass\n")
        cls = nodes_of_kind(graph, NodeKind.CLASS)[0]
        inherits = [
            r for r in graph.relations
            if r.source_id == cls.id and r.kind == RelationKind.INHERITS_FROM
        ]
        assert len(inherits) == 1

    def test_decorated_class(self):
        graph, _ = parse_and_visit("@dataclass\nclass Foo:\n    pass\n")
        cls = nodes_of_kind(graph, NodeKind.CLASS)[0]
        assert "dataclass" in cls.metadata["decorators"]

    def test_multiple_decorators(self):
        graph, _ = parse_and_visit("@decorator_a\n@decorator_b\nclass Foo:\n    pass\n")
        cls = nodes_of_kind(graph, NodeKind.CLASS)[0]
        decorators = cls.metadata["decorators"]
        assert "decorator_a" in decorators
        assert "decorator_b" in decorators


# ---------------------------------------------------------------------------
# Function extraction
# ---------------------------------------------------------------------------


class TestFunctionExtraction:
    def test_simple_function(self):
        graph, _ = parse_and_visit("def foo():\n    pass\n")
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        assert len(funcs) == 1
        assert funcs[0].name == "foo"

    def test_function_qualified_name(self):
        graph, _ = parse_and_visit("def bar():\n    pass\n", module_qname="mypkg.mod")
        func = nodes_of_kind(graph, NodeKind.FUNCTION)[0]
        assert func.qualified_name == "mypkg.mod.bar"

    def test_async_function(self):
        graph, _ = parse_and_visit("async def afoo():\n    pass\n")
        func = nodes_of_kind(graph, NodeKind.FUNCTION)[0]
        assert func.metadata["is_async"] is True

    def test_sync_function_not_async(self):
        graph, _ = parse_and_visit("def foo():\n    pass\n")
        func = nodes_of_kind(graph, NodeKind.FUNCTION)[0]
        assert func.metadata["is_async"] is False

    def test_method_inside_class(self):
        src = "class C:\n    def method(self):\n        pass\n"
        graph, _ = parse_and_visit(src)
        methods = nodes_of_kind(graph, NodeKind.METHOD)
        assert len(methods) == 1
        assert methods[0].name == "method"

    def test_classmethod(self):
        src = "class C:\n    @classmethod\n    def create(cls):\n        pass\n"
        graph, _ = parse_and_visit(src)
        method = nodes_of_kind(graph, NodeKind.METHOD)[0]
        assert method.metadata["is_classmethod"] is True

    def test_staticmethod(self):
        src = "class C:\n    @staticmethod\n    def util():\n        pass\n"
        graph, _ = parse_and_visit(src)
        method = nodes_of_kind(graph, NodeKind.METHOD)[0]
        assert method.metadata["is_staticmethod"] is True

    def test_property(self):
        src = "class C:\n    @property\n    def value(self):\n        return self._v\n"
        graph, _ = parse_and_visit(src)
        method = nodes_of_kind(graph, NodeKind.METHOD)[0]
        assert method.metadata["is_property"] is True

    def test_return_annotation(self):
        graph, _ = parse_and_visit("def foo() -> int:\n    return 1\n")
        func = nodes_of_kind(graph, NodeKind.FUNCTION)[0]
        assert func.metadata["return_annotation"] is not None

    def test_no_return_annotation(self):
        graph, _ = parse_and_visit("def foo():\n    pass\n")
        func = nodes_of_kind(graph, NodeKind.FUNCTION)[0]
        assert func.metadata["return_annotation"] is None

    def test_function_declares_relation(self):
        graph, file_id = parse_and_visit("def foo():\n    pass\n")
        func = nodes_of_kind(graph, NodeKind.FUNCTION)[0]
        declares = [
            r for r in graph.relations
            if r.source_id == file_id and r.target_id == func.id
            and r.kind == RelationKind.DECLARES
        ]
        assert len(declares) == 1

    def test_decorated_function(self):
        graph, _ = parse_and_visit("@my_decorator\ndef foo():\n    pass\n")
        func = nodes_of_kind(graph, NodeKind.FUNCTION)[0]
        assert "my_decorator" in func.metadata["decorators"]


# ---------------------------------------------------------------------------
# Parameter extraction
# ---------------------------------------------------------------------------


class TestParameterExtraction:
    def test_simple_param(self):
        graph, _ = parse_and_visit("def foo(x):\n    pass\n")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        names = {p.name for p in params}
        assert "x" in names

    def test_typed_param(self):
        graph, _ = parse_and_visit("def foo(x: int):\n    pass\n")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        x = next(p for p in params if p.name == "x")
        assert x.metadata["annotation"] is not None

    def test_default_param(self):
        graph, _ = parse_and_visit("def foo(x=10):\n    pass\n")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        x = next(p for p in params if p.name == "x")
        assert x.metadata["has_default"] is True

    def test_typed_default_param(self):
        graph, _ = parse_and_visit("def foo(x: int = 0):\n    pass\n")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        x = next(p for p in params if p.name == "x")
        assert x.metadata["has_default"] is True
        assert x.metadata["annotation"] is not None

    def test_args_variadic(self):
        graph, _ = parse_and_visit("def foo(*args):\n    pass\n")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        args_param = next(p for p in params if p.name == "args")
        assert args_param.metadata["is_variadic"] is True

    def test_kwargs_variadic(self):
        graph, _ = parse_and_visit("def foo(**kwargs):\n    pass\n")
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        kw = next(p for p in params if p.name == "kwargs")
        assert kw.metadata["is_variadic"] is True

    def test_self_detected(self):
        src = "class C:\n    def method(self):\n        pass\n"
        graph, _ = parse_and_visit(src)
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        self_param = next(p for p in params if p.name == "self")
        assert self_param.metadata["is_self"] is True

    def test_cls_detected(self):
        src = "class C:\n    @classmethod\n    def create(cls):\n        pass\n"
        graph, _ = parse_and_visit(src)
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        cls_param = next(p for p in params if p.name == "cls")
        assert cls_param.metadata["is_cls"] is True

    def test_params_declared_by_function(self):
        graph, _ = parse_and_visit("def foo(a, b):\n    pass\n")
        func = nodes_of_kind(graph, NodeKind.FUNCTION)[0]
        params = nodes_of_kind(graph, NodeKind.PARAMETER)
        param_ids = {p.id for p in params}
        declared = {
            r.target_id for r in graph.relations
            if r.source_id == func.id and r.kind == RelationKind.DECLARES
        }
        assert param_ids == declared


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------


class TestImportExtraction:
    def test_simple_import(self):
        classifier = ImportClassifier(stdlib=frozenset({"os"}))
        graph, _ = parse_and_visit("import os\n", classifier=classifier)
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        assert any(i.name == "os" for i in imports)
        os_import = next(i for i in imports if i.name == "os")
        assert os_import.metadata["origin"] == "stdlib"

    def test_import_as(self):
        graph, _ = parse_and_visit("import os as operating_system\n")
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "operating_system")
        assert imp.metadata["alias"] == "operating_system"

    def test_from_import(self):
        classifier = ImportClassifier(stdlib=frozenset({"pathlib"}))
        graph, _ = parse_and_visit("from pathlib import Path\n", classifier=classifier)
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "Path")
        assert imp.metadata["origin"] == "stdlib"

    def test_from_import_as(self):
        graph, _ = parse_and_visit("from os import path as ospath\n")
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "ospath")
        assert imp.metadata["alias"] == "ospath"

    def test_relative_import(self):
        graph, _ = parse_and_visit(
            "from . import utils\n", module_qname="mypkg.models"
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "utils")
        assert imp.metadata["is_relative"] is True
        assert imp.metadata["origin"] == "internal"

    def test_relative_import_level_2(self):
        graph, _ = parse_and_visit(
            "from .. import base\n", module_qname="mypkg.sub.mod"
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "base")
        assert imp.metadata["level"] == 2

    def test_wildcard_import(self):
        graph, _ = parse_and_visit("from os import *\n")
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        star = next(i for i in imports if i.name == "*")
        assert star.metadata["is_star"] is True

    def test_third_party_import(self):
        classifier = ImportClassifier(third_party=frozenset({"requests"}))
        graph, _ = parse_and_visit("import requests\n", classifier=classifier)
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "requests")
        assert imp.metadata["origin"] == "third_party"

    def test_unknown_import(self):
        graph, _ = parse_and_visit("import some_unknown_pkg\n")
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "some_unknown_pkg")
        assert imp.metadata["origin"] == "unknown"

    def test_import_resolves_to_module_when_in_graph(self):
        """Internal import resolves to MODULE node if already in graph."""
        source = "from mypkg import utils\n"
        source_bytes = source.encode()
        tree = parse_python(source_bytes)

        graph = CodeGraph()
        # Pre-populate a MODULE node for mypkg
        mod_id = make_node_id("mypkg", "mypkg", NodeKind.MODULE.value)
        from code_graph import Node as CGNode
        graph.add_node(
            CGNode(id=mod_id, kind=NodeKind.MODULE, qualified_name="mypkg", name="mypkg")
        )
        file_id = make_node_id("mypkg", "src/mypkg/mod.py", NodeKind.FILE.value)
        graph.add_node(
            CGNode(
                id=file_id, kind=NodeKind.FILE,
                qualified_name="src/mypkg/mod.py", name="mod.py",
                file_path="src/mypkg/mod.py",
            )
        )

        classifier = ImportClassifier(internal=frozenset({"mypkg"}))
        ctx = VisitorContext(
            project_name="mypkg",
            file_path=Path("src/mypkg/mod.py"),
            source_root=Path("src"),
            module_qualified_name="mypkg.mod",
        )
        visitor = PythonASTVisitor(ctx, graph, file_id, source_bytes, classifier)
        visitor.visit(tree.root_node)

        resolves = [r for r in graph.relations if r.kind == RelationKind.RESOLVES_TO]
        assert any(r.target_id == mod_id for r in resolves)

    def test_from_relative_import_with_module(self):
        graph, _ = parse_and_visit(
            "from .utils import helper\n", module_qname="mypkg.models"
        )
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = next(i for i in imports if i.name == "helper")
        assert imp.metadata["is_relative"] is True

    def test_import_has_resolves_to_relation(self):
        graph, _ = parse_and_visit("import os\n")
        imports = nodes_of_kind(graph, NodeKind.IMPORT)
        imp = imports[0]
        resolves = [r for r in graph.relations if r.source_id == imp.id and r.kind == RelationKind.RESOLVES_TO]
        assert len(resolves) == 1

    def test_file_has_imports_relation(self):
        graph, file_id = parse_and_visit("import os\n")
        imports_rels = [
            r for r in graph.relations
            if r.source_id == file_id and r.kind == RelationKind.IMPORTS
        ]
        assert len(imports_rels) == 1


# ---------------------------------------------------------------------------
# Call extraction
# ---------------------------------------------------------------------------


class TestCallExtraction:
    def test_simple_call(self):
        src = "def foo():\n    bar()\n"
        graph, _ = parse_and_visit(src)
        calls = [r for r in graph.relations if r.kind == RelationKind.CALLS]
        assert len(calls) == 1

    def test_method_call(self):
        src = "def foo():\n    obj.method()\n"
        graph, _ = parse_and_visit(src)
        calls = [r for r in graph.relations if r.kind == RelationKind.CALLS]
        assert len(calls) == 1
        sym_id = calls[0].target_id
        sym = graph.nodes[sym_id]
        assert "method" in sym.name

    def test_multiple_calls(self):
        src = "def foo():\n    a()\n    b()\n    c()\n"
        graph, _ = parse_and_visit(src)
        calls = [r for r in graph.relations if r.kind == RelationKind.CALLS]
        assert len(calls) == 3

    def test_call_not_extracted_from_nested_function(self):
        src = "def outer():\n    def inner():\n        nested_call()\n"
        graph, _ = parse_and_visit(src)
        outer = next(f for f in nodes_of_kind(graph, NodeKind.FUNCTION) if f.name == "outer")
        calls_from_outer = [r for r in graph.relations if r.source_id == outer.id and r.kind == RelationKind.CALLS]
        assert len(calls_from_outer) == 0

    def test_call_symbol_node_created(self):
        src = "def foo():\n    my_func()\n"
        graph, _ = parse_and_visit(src)
        symbols = nodes_of_kind(graph, NodeKind.SYMBOL)
        assert any(s.name == "my_func" for s in symbols)


# ---------------------------------------------------------------------------
# Nested definitions
# ---------------------------------------------------------------------------


class TestNestedDefinitions:
    def test_nested_function(self):
        src = "def outer():\n    def inner():\n        pass\n"
        graph, _ = parse_and_visit(src)
        funcs = nodes_of_kind(graph, NodeKind.FUNCTION)
        names = {f.name for f in funcs}
        assert "outer" in names
        assert "inner" in names

    def test_nested_class(self):
        src = "class Outer:\n    class Inner:\n        pass\n"
        graph, _ = parse_and_visit(src)
        classes = nodes_of_kind(graph, NodeKind.CLASS)
        names = {c.name for c in classes}
        assert "Outer" in names
        assert "Inner" in names

    def test_nested_qualified_names(self):
        src = "class Outer:\n    def method(self):\n        pass\n"
        graph, _ = parse_and_visit(src, module_qname="pkg.mod")
        method = nodes_of_kind(graph, NodeKind.METHOD)[0]
        assert method.qualified_name == "pkg.mod.Outer.method"


# ---------------------------------------------------------------------------
# Span generation
# ---------------------------------------------------------------------------


class TestSpanGeneration:
    def test_spans_are_1_based(self):
        graph, _ = parse_and_visit("def foo():\n    pass\n")
        func = nodes_of_kind(graph, NodeKind.FUNCTION)[0]
        assert func.span is not None
        assert func.span.start_line >= 1
        assert func.span.start_col >= 1

    def test_multiline_span(self):
        src = "def foo():\n    x = 1\n    return x\n"
        graph, _ = parse_and_visit(src)
        func = nodes_of_kind(graph, NodeKind.FUNCTION)[0]
        assert func.span.end_line > func.span.start_line


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


class TestMakeSpan:
    def test_none_returns_none(self):
        assert _make_span(None) is None

    def test_valid_node(self):
        source = b"x = 1\n"
        tree = parse_python(source)
        # Get any real node with positions
        span = _make_span(tree.root_node)
        assert span is not None
        assert span.start_line >= 1

    def test_exception_returns_none(self):
        mock_node = MagicMock()
        type(mock_node).start_point = PropertyMock(side_effect=Exception("boom"))
        assert _make_span(mock_node) is None


class TestNameFromNode:
    def test_identifier(self):
        source = b"foo\n"
        tree = parse_python(source)
        # Find the identifier node
        id_node = tree.root_node.children[0].children[0]
        assert id_node.type == "identifier"
        assert _name_from_node(id_node) == "foo"

    def test_attribute(self):
        source = b"obj.attr\n"
        tree = parse_python(source)
        # Navigate to attribute node
        expr = tree.root_node.children[0].children[0]
        assert expr.type == "attribute"
        assert _name_from_node(expr) == "obj.attr"

    def test_other_type_returns_empty(self):
        mock_node = MagicMock()
        mock_node.type = "string"
        result = _name_from_node(mock_node)
        assert result == ""

    def test_attribute_empty_parent(self):
        """Attribute where first child is not identifier/attribute → returns just attr."""
        mock_attr = MagicMock()
        mock_attr.type = "attribute"
        mock_first = MagicMock()
        mock_first.type = "unknown_type"
        mock_last = MagicMock()
        mock_last.text = b"method"
        mock_attr.children = [mock_first, mock_last]
        result = _name_from_node(mock_attr)
        assert result == "method"


class TestDecoratorName:
    def test_identifier_decorator(self):
        source = b"@decorator\ndef foo(): pass\n"
        tree = parse_python(source)
        decorated = tree.root_node.children[0]
        decorator_node = next(c for c in decorated.children if c.type == "decorator")
        name = _decorator_name(decorator_node)
        assert name == "decorator"

    def test_attribute_decorator(self):
        source = b"@module.decorator\ndef foo(): pass\n"
        tree = parse_python(source)
        decorated = tree.root_node.children[0]
        decorator_node = next(c for c in decorated.children if c.type == "decorator")
        name = _decorator_name(decorator_node)
        assert name == "module.decorator"

    def test_call_decorator_returns_empty(self):
        """A call decorator with no identifier child returns ''."""
        mock_dec = MagicMock()
        mock_child = MagicMock()
        mock_child.type = "call"
        # _name_from_node("call") returns "" → if name: is False → return ""
        mock_dec.children = [mock_child]
        assert _decorator_name(mock_dec) == ""

    def test_no_matching_children_returns_empty(self):
        mock_dec = MagicMock()
        mock_dec.children = []
        assert _decorator_name(mock_dec) == ""


class TestFindModuleNodeId:
    def test_exact_match(self):
        graph = CodeGraph()
        from code_graph import Node as CGNode
        mod_id = make_node_id("proj", "mypkg.utils", NodeKind.MODULE.value)
        graph.add_node(
            CGNode(id=mod_id, kind=NodeKind.MODULE, qualified_name="mypkg.utils", name="utils")
        )
        result = _find_module_node_id(graph, "mypkg.utils")
        assert result == mod_id

    def test_prefix_match(self):
        graph = CodeGraph()
        from code_graph import Node as CGNode
        mod_id = make_node_id("proj", "mypkg", NodeKind.MODULE.value)
        graph.add_node(
            CGNode(id=mod_id, kind=NodeKind.MODULE, qualified_name="mypkg", name="mypkg")
        )
        result = _find_module_node_id(graph, "mypkg.utils.helper")
        assert result == mod_id

    def test_not_found_returns_none(self):
        graph = CodeGraph()
        assert _find_module_node_id(graph, "nonexistent.module") is None

    def test_non_module_node_not_matched(self):
        graph = CodeGraph()
        from code_graph import Node as CGNode
        class_id = make_node_id("proj", "mypkg", NodeKind.CLASS.value)
        graph.add_node(
            CGNode(id=class_id, kind=NodeKind.CLASS, qualified_name="mypkg", name="mypkg")
        )
        assert _find_module_node_id(graph, "mypkg") is None


class TestHandleClassNoIdentifier:
    def test_class_without_identifier_skipped(self):
        """Covers _handle_class: if name_node is None: return."""
        graph = CodeGraph()
        file_id = make_node_id("proj", "mod.py", NodeKind.FILE.value)
        from code_graph import Node as CGNode
        graph.add_node(CGNode(id=file_id, kind=NodeKind.FILE, qualified_name="mod.py", name="mod.py"))
        ctx = VisitorContext(
            project_name="proj",
            file_path=Path("mod.py"),
            source_root=Path("."),
            module_qualified_name="mod",
        )
        visitor = PythonASTVisitor(ctx, graph, file_id, b"", None)

        # class_definition node with no identifier child
        mock_node = MagicMock()
        mock_node.children = [MagicMock(type="block")]  # no identifier
        visitor._handle_class(mock_node, decorators=[])
        # Should return without adding any class nodes
        assert len(nodes_of_kind(graph, NodeKind.CLASS)) == 0


class TestHandleFunctionNoIdentifier:
    def test_function_without_identifier_skipped(self):
        """Covers _handle_function: if name_node is None: return."""
        graph = CodeGraph()
        file_id = make_node_id("proj", "mod.py", NodeKind.FILE.value)
        from code_graph import Node as CGNode
        graph.add_node(CGNode(id=file_id, kind=NodeKind.FILE, qualified_name="mod.py", name="mod.py"))
        ctx = VisitorContext(
            project_name="proj",
            file_path=Path("mod.py"),
            source_root=Path("."),
            module_qualified_name="mod",
        )
        visitor = PythonASTVisitor(ctx, graph, file_id, b"", None)

        # function_definition node with no identifier child
        mock_node = MagicMock()
        mock_node.children = [MagicMock(type="block")]  # no identifier
        visitor._handle_function(mock_node, decorators=[])
        # Should return without adding any function nodes
        assert len(nodes_of_kind(graph, NodeKind.FUNCTION)) == 0


class TestDecoratedDefinitionEdgeCases:
    def test_decorated_definition_no_class_or_function(self):
        """Coverage for `if inner is None: return` branch."""
        source = b"x = 1\n"
        parse_python(source)
        graph = CodeGraph()
        file_id = make_node_id("proj", "mod.py", NodeKind.FILE.value)
        from code_graph import Node as CGNode
        graph.add_node(
            CGNode(id=file_id, kind=NodeKind.FILE, qualified_name="mod.py", name="mod.py")
        )
        ctx = VisitorContext(
            project_name="proj",
            file_path=Path("mod.py"),
            source_root=Path("."),
            module_qualified_name="mod",
        )
        visitor = PythonASTVisitor(ctx, graph, file_id, source, None)

        # Create a mock decorated_definition with no class/function child
        mock_node = MagicMock()
        mock_decorator = MagicMock()
        mock_decorator.type = "decorator"
        mock_other = MagicMock()
        mock_other.type = "expression_statement"  # not class or function
        mock_node.children = [mock_decorator, mock_other]
        # Should return without error
        visitor._visit_decorated_definition(mock_node)
