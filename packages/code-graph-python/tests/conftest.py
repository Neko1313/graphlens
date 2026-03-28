"""Shared fixtures for Python adapter tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from code_graph import CodeGraph, Node, NodeKind
from code_graph.utils.ids import make_node_id

from code_graph_python._visitor import (
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


def parse_and_visit(
    source: str,
    module_qname: str = "mypkg.mod",
    project_name: str = "mypkg",
    classifier: ImportClassifier | None = None,
) -> tuple[CodeGraph, str]:
    """Parse Python source and run the visitor; returns (graph, file_node_id)."""
    source_bytes = source.encode("utf-8")
    tree = parse_python(source_bytes)

    graph = CodeGraph()
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


def nodes_of_kind(graph: CodeGraph, kind: NodeKind) -> list[Node]:
    return [n for n in graph.nodes.values() if n.kind == kind]
