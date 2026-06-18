"""Shared fixtures for Python adapter tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from graphlens import GraphLens, Node, NodeKind
from graphlens.utils.ids import make_node_id

from graphlens_python._visitor import (
    ImportClassifier,
    PythonASTVisitor,
    VisitorContext,
    parse_python,
)


@pytest.fixture
def sample_python_project(tmp_path: Path) -> Path:
    """Create a minimal Python project with src-layout."""
    # pyproject.toml
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "mypkg"\nversion = "0.1.0"\n'
        'dependencies = ["requests>=2.0"]\n'
        '[project.optional-dependencies]\ntest = ["pytest"]\n'
    )

    # Source layout
    src = tmp_path / "src" / "mypkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text('"""mypkg package."""\n')

    (src / "utils.py").write_text(
        "import os\nimport sys\nfrom pathlib import Path\n\n"
        "def helper(x: int) -> str:\n    return str(x)\n"
    )

    (src / "models.py").write_text(
        "import requests\nfrom mypkg.utils import helper\n\n"
        "class MyModel:\n    def __init__(self, value: int) -> None:\n"
        "        self.value = helper(value)\n\n"
        "    def process(self) -> str:\n        return str(self.value)\n"
    )

    return tmp_path


def make_file_node(project_name: str, relative_path: str, file_path: Path) -> Node:
    """Create a FILE node for use in visitor tests."""
    file_id = make_node_id(project_name, relative_path, NodeKind.FILE.value)
    return Node(
        id=file_id,
        kind=NodeKind.FILE,
        qualified_name=relative_path,
        name=file_path.name,
        file_path=relative_path,
    )


def _run_visitor(
    source: str,
    module_qname: str = "mypkg.mod",
    project_name: str = "mypkg",
    classifier: ImportClassifier | None = None,
) -> tuple[GraphLens, PythonASTVisitor]:
    """
    Core helper: parse Python source, run the visitor.

    Returns ``(graph, visitor)`` so callers can inspect both the graph
    and occurrence data on the visitor.
    """
    source_bytes = source.encode("utf-8")
    tree = parse_python(source_bytes)

    graph = GraphLens()
    file_id = make_node_id(project_name, "src/mypkg/mod.py", NodeKind.FILE.value)
    file_node = Node(
        id=file_id,
        kind=NodeKind.FILE,
        qualified_name="src/mypkg/mod.py",
        name="mod.py",
        file_path="src/mypkg/mod.py",
    )
    graph.add_node(file_node)

    ctx = VisitorContext(
        project_name=project_name,
        file_path=Path("src/mypkg/mod.py"),
        source_root=Path("src"),
        module_qualified_name=module_qname,
    )
    visitor = PythonASTVisitor(ctx, graph, file_id, source_bytes, classifier)
    visitor.visit(tree.root_node)

    return graph, visitor


def parse_and_visit(
    source: str,
    module_qname: str = "mypkg.mod",
    project_name: str = "mypkg",
    classifier: ImportClassifier | None = None,
) -> tuple[GraphLens, str]:
    """
    Parse Python source and run the visitor.

    Returns ``(graph, file_node_id)`` — kept for backward compatibility with
    existing test code that imports this function directly.
    """
    source_bytes = source.encode("utf-8")
    tree = parse_python(source_bytes)

    graph = GraphLens()
    file_id = make_node_id(project_name, "src/mypkg/mod.py", NodeKind.FILE.value)
    file_node = Node(
        id=file_id,
        kind=NodeKind.FILE,
        qualified_name="src/mypkg/mod.py",
        name="mod.py",
        file_path="src/mypkg/mod.py",
    )
    graph.add_node(file_node)

    ctx = VisitorContext(
        project_name=project_name,
        file_path=Path("src/mypkg/mod.py"),
        source_root=Path("src"),
        module_qualified_name=module_qname,
    )
    visitor = PythonASTVisitor(ctx, graph, file_id, source_bytes, classifier)
    visitor.visit(tree.root_node)

    return graph, file_id


def nodes_of_kind(graph: GraphLens, kind: NodeKind) -> list[Node]:
    """Return all nodes of the given kind from the graph."""
    return [n for n in graph.nodes.values() if n.kind == kind]


# ---------------------------------------------------------------------------
# pytest fixtures (callable factories so individual tests can pass source)
# ---------------------------------------------------------------------------


@pytest.fixture(name="parse_and_visit")
def _fixture_parse_and_visit():
    """
    Fixture: returns a callable that parses source and returns just the graph.

    Usage::

        def test_foo(parse_and_visit):
            graph = parse_and_visit("class Foo:\\n    pass\\n")
    """
    def _factory(
        source: str,
        module_qname: str = "mypkg.mod",
        project_name: str = "mypkg",
        classifier: ImportClassifier | None = None,
    ) -> GraphLens:
        graph, _visitor = _run_visitor(
            source, module_qname, project_name, classifier
        )
        return graph

    return _factory


@pytest.fixture(name="parse_and_visit_visitor")
def _fixture_parse_and_visit_visitor():
    """
    Fixture: returns a callable that parses source and returns ``(graph, visitor)``.

    Usage::

        def test_foo(parse_and_visit_visitor):
            graph, visitor = parse_and_visit_visitor("def a():\\n    pass\\n")
    """
    def _factory(
        source: str,
        module_qname: str = "mypkg.mod",
        project_name: str = "mypkg",
        classifier: ImportClassifier | None = None,
    ) -> tuple[GraphLens, PythonASTVisitor]:
        return _run_visitor(source, module_qname, project_name, classifier)

    return _factory
