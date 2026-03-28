"""Shared test fixtures for graphlens-typescript."""

from __future__ import annotations

from pathlib import Path

import pytest
from graphlens import GraphLens, Node, NodeKind
from graphlens.utils.ids import make_node_id

from graphlens_typescript._visitor import (
    ImportClassifier,
    TypescriptASTVisitor,
    VisitorContext,
    parse_typescript,
)


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Return a temporary directory ready to receive TypeScript project files."""
    return tmp_path


@pytest.fixture
def sample_typescript_project(tmp_path: Path) -> Path:
    """Create a minimal TypeScript project with src-layout."""
    (tmp_path / "package.json").write_text(
        '{"name": "my-ts-app", "version": "1.0.0",'
        ' "dependencies": {"lodash": "^4.0.0"},'
        ' "devDependencies": {"typescript": "^5.0.0", "jest": "^29.0.0"}}'
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions": {"target": "ES2020", "module": "commonjs"}}'
    )

    src = tmp_path / "src" / "myapp"
    src.mkdir(parents=True)

    (src / "index.ts").write_text(
        'export { greet } from "./utils";\n'
        'export { MyService } from "./service";\n'
    )

    (src / "utils.ts").write_text(
        'import path from "path";\n'
        'import { join } from "path";\n\n'
        "export function greet(name: string): string {\n"
        '    return `Hello, ${name}!`;\n'
        "}\n\n"
        "export function resolvePath(p: string): string {\n"
        "    return join(path.resolve(p));\n"
        "}\n"
    )

    (src / "service.ts").write_text(
        'import { greet } from "./utils";\n'
        'import lodash from "lodash";\n\n'
        "export class MyService {\n"
        "    private name: string;\n\n"
        "    constructor(name: string) {\n"
        "        this.name = name;\n"
        "    }\n\n"
        "    public greetUser(): string {\n"
        "        return greet(lodash.trim(this.name));\n"
        "    }\n"
        "}\n"
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
    module_qname: str = "myapp.mod",
    project_name: str = "myapp",
    classifier: ImportClassifier | None = None,
    *,
    tsx: bool = False,
) -> tuple[GraphLens, str]:
    """Parse TypeScript source and run the visitor; returns (graph, file_node_id)."""
    source_bytes = source.encode("utf-8")
    tree = parse_typescript(source_bytes, tsx=tsx)

    graph = GraphLens()
    rel_path = "src/myapp/mod.tsx" if tsx else "src/myapp/mod.ts"
    file_id = make_node_id(project_name, rel_path, NodeKind.FILE.value)
    file_node = Node(
        id=file_id,
        kind=NodeKind.FILE,
        qualified_name=rel_path,
        name="mod.tsx" if tsx else "mod.ts",
        file_path=rel_path,
    )
    graph.add_node(file_node)

    ctx = VisitorContext(
        project_name=project_name,
        file_path=Path(rel_path),
        source_root=Path("src"),
        module_qualified_name=module_qname,
    )
    visitor = TypescriptASTVisitor(ctx, graph, file_id, source_bytes, classifier)
    visitor.visit(tree.root_node)

    return graph, file_id


def nodes_of_kind(graph: GraphLens, kind: NodeKind) -> list[Node]:
    return [n for n in graph.nodes.values() if n.kind == kind]
