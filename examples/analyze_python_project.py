"""
Example: analyze a project using all available graphlens adapters.

Adapters are resolved automatically from installed entry points —
no direct imports of language-specific packages are needed.
Works for single-language and multi-language (polyglot) projects alike.

Usage:
    uv run python examples/analyze_python_project.py <path_to_project>

Or analyze this repo itself:
    uv run python examples/analyze_python_project.py packages/graphlens-python
"""

from __future__ import annotations

import sys
from pathlib import Path

from graphlens import NodeKind, RelationKind, adapter_registry
from graphlens.models import GraphLens


def main(project_root: Path) -> None:
    available = adapter_registry.available()
    if not available:
        print("No adapters installed.")
        sys.exit(1)

    graphs: list[tuple[str, GraphLens]] = []
    for lang in available:
        adapter = adapter_registry.load(lang)()
        if adapter.can_handle(project_root):
            graphs.append((lang, adapter.analyze(project_root)))

    if not graphs:
        print(f"No adapter can handle: {project_root}")
        sys.exit(1)

    print(f"Analyzed: {project_root}\n")

    for lang, graph in graphs:
        print(f"{'=' * 40}")
        print(f"Language: {lang}")
        print(f"{'=' * 40}")
        _print_graph(graph)


def _print_graph(graph: GraphLens) -> None:
    by_kind: dict[NodeKind, int] = {}
    for node in graph.nodes.values():
        by_kind[node.kind] = by_kind.get(node.kind, 0) + 1

    by_relation: dict[RelationKind, int] = {}
    for rel in graph.relations:
        by_relation[rel.kind] = by_relation.get(rel.kind, 0) + 1

    print("=== Nodes ===")
    for kind, count in sorted(by_kind.items(), key=lambda x: -x[1]):
        print(f"  {kind.value:<20} {count}")
    print(f"\n  Total: {len(graph.nodes)} nodes")

    print("\n=== Relations ===")
    for kind, count in sorted(by_relation.items(), key=lambda x: -x[1]):
        print(f"  {kind.value:<20} {count}")
    print(f"\n  Total: {len(graph.relations)} relations")

    # Classes with methods
    print("\n=== Classes ===")
    classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
    for cls in sorted(classes, key=lambda n: n.qualified_name):
        bases = cls.metadata.get("bases", [])
        decorators = cls.metadata.get("decorators", [])
        base_str = f"({', '.join(bases)})" if bases else ""
        dec_str = f"  [{', '.join(decorators)}]" if decorators else ""
        print(f"\n  class {cls.qualified_name}{base_str}{dec_str}")

        declared_ids = {
            r.target_id for r in graph.relations
            if r.source_id == cls.id and r.kind == RelationKind.DECLARES
        }
        methods = [
            graph.nodes[nid]
            for nid in declared_ids
            if nid in graph.nodes and graph.nodes[nid].kind == NodeKind.METHOD
        ]
        for method in sorted(methods, key=lambda n: n.name):
            async_str = "async " if method.metadata.get("is_async") else ""
            ret = method.metadata.get("return_annotation")
            ret_str = f" -> {ret}" if ret else ""
            print(f"    {async_str}def {method.name}(){ret_str}")

    # Top-level functions
    print("\n=== Functions ===")
    functions = [n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION]
    for func in sorted(functions, key=lambda n: n.qualified_name)[:20]:
        async_str = "async " if func.metadata.get("is_async") else ""
        ret = func.metadata.get("return_annotation")
        ret_str = f" -> {ret}" if ret else ""
        print(f"  {async_str}def {func.qualified_name}(){ret_str}")
    if len(functions) > 20:
        print(f"  ... and {len(functions) - 20} more")

    # External dependencies (top-level package names)
    print("\n=== External Dependencies ===")
    ext_symbols = {
        n.qualified_name.split(".")[0]
        for n in graph.nodes.values()
        if n.kind == NodeKind.EXTERNAL_SYMBOL
    }
    for dep in sorted(ext_symbols):
        print(f"  {dep}")


if __name__ == "__main__":
    root = Path(__file__).parent.parent if len(sys.argv) < 2 else Path(sys.argv[1]).resolve()
    main(root)
