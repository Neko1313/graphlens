"""
Example: analyze a TypeScript project with graphlens.

Shows module structure, class hierarchy, import classification,
and call graph — TypeScript-specific metadata included.

Usage:
    uv run python examples/analyze_typescript_project.py <path_to_project>

Or analyze the built-in demo project:
    uv run python examples/analyze_typescript_project.py examples/demo-ts-project
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from graphlens import NodeKind, RelationKind, adapter_registry
from graphlens.models import GraphLens


def main(project_root: Path) -> None:
    adapter = adapter_registry.load("typescript")()

    if not adapter.can_handle(project_root):
        print(f"Not a TypeScript project: {project_root}")
        sys.exit(1)

    print(f"Analyzing: {project_root}\n")
    graph = adapter.analyze(project_root)
    print(f"Graph built: {len(graph.nodes)} nodes, {len(graph.relations)} relations\n")

    _print_summary(graph)
    _print_modules(graph)
    _print_classes(graph)
    _print_functions(graph)
    _print_imports(graph)
    _print_calls(graph)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _print_summary(graph: GraphLens) -> None:
    kind_counts: Counter[str] = Counter(
        n.kind.value for n in graph.nodes.values()
    )
    rel_counts: Counter[str] = Counter(r.kind.value for r in graph.relations)

    _header("Summary")
    _header("Nodes", level=2)
    for kind, count in kind_counts.most_common():
        print(f"  {kind:<22} {count}")

    _header("Relations", level=2)
    for kind, count in rel_counts.most_common():
        print(f"  {kind:<22} {count}")


# ---------------------------------------------------------------------------
# Module tree
# ---------------------------------------------------------------------------


def _print_modules(graph: GraphLens) -> None:
    _header("Module tree")

    project_nodes = [
        n for n in graph.nodes.values() if n.kind == NodeKind.PROJECT
    ]
    for project in sorted(project_nodes, key=lambda n: n.name):
        print(f"  project: {project.name}")
        _print_module_children(graph, project.id, indent=4)


def _print_module_children(
    graph: GraphLens, parent_id: str, indent: int
) -> None:
    children = [
        graph.nodes[r.target_id]
        for r in graph.relations
        if r.source_id == parent_id
        and r.kind == RelationKind.CONTAINS
        and r.target_id in graph.nodes
    ]
    for child in sorted(children, key=lambda n: n.qualified_name):
        icon = {
            NodeKind.MODULE: "📦",
            NodeKind.FILE: "📄",
        }.get(child.kind, "  ")
        print(f"{' ' * indent}{icon} {child.qualified_name}")
        if child.kind == NodeKind.MODULE:
            _print_module_children(graph, child.id, indent + 2)


# ---------------------------------------------------------------------------
# Classes and interfaces
# ---------------------------------------------------------------------------


def _print_classes(graph: GraphLens) -> None:
    _header("Classes & Interfaces")

    classes = [n for n in graph.nodes.values() if n.kind == NodeKind.CLASS]
    if not classes:
        print("  (none)")
        return

    for cls in sorted(classes, key=lambda n: n.qualified_name):
        is_interface = cls.metadata.get("is_interface", False)
        is_abstract = cls.metadata.get("is_abstract", False)
        bases = cls.metadata.get("bases", [])
        decorators = cls.metadata.get("decorators", [])

        kind_str = "interface" if is_interface else (
            "abstract class" if is_abstract else "class"
        )
        base_str = (
            f" extends {', '.join(bases)}" if bases else ""
        )
        dec_str = (
            f"  [{', '.join(decorators)}]" if decorators else ""
        )

        loc = (
            f"  ({cls.file_path}:{cls.span.start_line})"
            if cls.file_path and cls.span else ""
        )

        print(f"\n  {kind_str} {cls.qualified_name}{base_str}{dec_str}{loc}")

        # Methods
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
            ret = method.metadata.get("return_annotation") or ""
            ret_str = f": {ret}" if ret else ""
            params = _param_str(graph, method.id)
            print(f"    {async_str}{method.name}({params}){ret_str}")


def _param_str(graph: GraphLens, func_id: str) -> str:
    param_ids = [
        r.target_id for r in graph.relations
        if r.source_id == func_id and r.kind == RelationKind.DECLARES
        and r.target_id in graph.nodes
        and graph.nodes[r.target_id].kind == NodeKind.PARAMETER
    ]
    params = []
    for pid in param_ids:
        p = graph.nodes[pid]
        ann = p.metadata.get("annotation") or ""
        suffix = "?" if p.metadata.get("has_default") else ""
        prefix = "..." if p.metadata.get("is_variadic") else ""
        type_str = f": {ann}" if ann else ""
        params.append(f"{prefix}{p.name}{suffix}{type_str}")
    return ", ".join(params)


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def _print_functions(graph: GraphLens) -> None:
    _header("Functions")

    functions = [
        n for n in graph.nodes.values() if n.kind == NodeKind.FUNCTION
    ]
    if not functions:
        print("  (none)")
        return

    for func in sorted(functions, key=lambda n: n.qualified_name):
        async_str = "async " if func.metadata.get("is_async") else ""
        ret = func.metadata.get("return_annotation") or ""
        ret_str = f": {ret}" if ret else ""
        params = _param_str(graph, func.id)
        loc = (
            f"  ({func.file_path}:{func.span.start_line})"
            if func.file_path and func.span else ""
        )
        print(f"  {async_str}{func.qualified_name}({params}){ret_str}{loc}")


# ---------------------------------------------------------------------------
# Import classification
# ---------------------------------------------------------------------------


def _print_imports(graph: GraphLens) -> None:
    _header("Imports by origin")

    imports = [n for n in graph.nodes.values() if n.kind == NodeKind.IMPORT]
    if not imports:
        print("  (none)")
        return

    by_origin: dict[str, list[str]] = {}
    for imp in imports:
        origin = str(imp.metadata.get("origin", "unknown"))
        by_origin.setdefault(origin, []).append(
            imp.metadata.get("original_name") or imp.name
        )

    order = ["stdlib", "internal", "third_party", "unknown"]
    for origin in order:
        names = sorted(set(by_origin.get(origin, [])))
        if not names:
            continue
        print(f"\n  [{origin}]")
        for name in names:
            print(f"    {name}")


# ---------------------------------------------------------------------------
# Call graph (top callers)
# ---------------------------------------------------------------------------


def _print_calls(graph: GraphLens) -> None:
    _header("Call graph (top callers)")

    calls = [r for r in graph.relations if r.kind == RelationKind.CALLS]
    if not calls:
        print("  (no calls found)")
        return

    caller_counts: Counter[str] = Counter(r.source_id for r in calls)
    print(f"  Total call sites: {len(calls)}\n")

    for caller_id, count in caller_counts.most_common(10):
        if caller_id not in graph.nodes:
            continue
        caller = graph.nodes[caller_id]
        callees = [
            graph.nodes[r.target_id].name
            for r in calls
            if r.source_id == caller_id and r.target_id in graph.nodes
        ]
        print(f"  {caller.qualified_name}  ({count} calls)")
        for callee in sorted(set(callees)):
            print(f"    → {callee}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _header(title: str, level: int = 1) -> None:
    if level == 1:
        print(f"\n{'=' * 50}")
        print(f"  {title}")
        print(f"{'=' * 50}")
    else:
        print(f"\n  --- {title} ---")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        root = Path(__file__).parent / "demo-ts-project"
    else:
        root = Path(sys.argv[1]).resolve()
    main(root)
