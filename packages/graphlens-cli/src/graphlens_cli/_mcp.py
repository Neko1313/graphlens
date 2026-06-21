"""
graphlens mcp — serve the graph query API to agents over MCP (TCK-7).

The query logic lives in plain functions that operate on a ``GraphLens``
so it is fully testable without the optional ``mcp`` dependency.  The
``mcp`` package is imported lazily inside :func:`_build_server`, and the
``mcp`` subcommand prints a friendly hint if it is not installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from graphlens import RESOLVER_STATUS_KEY, GraphLens, NodeKind, RelationKind

from graphlens_cli._app import app

if TYPE_CHECKING:
    from graphlens import Node


def load_graph(path: Path) -> GraphLens:
    """Load a graph from a JSON file produced by ``analyze --output``."""
    return GraphLens.from_json(path.read_text(encoding="utf-8"))


def _node_dict(node: Node) -> dict[str, object]:
    return {
        "id": node.id,
        "kind": node.kind.value,
        "qualified_name": node.qualified_name,
        "name": node.name,
        "file_path": node.file_path,
    }


def _resolve_ids(graph: GraphLens, node: str) -> list[str]:
    """Resolve *node* (an id or a qualified/short name) to node ids."""
    if node in graph.nodes:
        return [node]
    return [n.id for n in graph.nodes_by_name(node)]


def graph_stats(graph: GraphLens) -> dict[str, object]:
    """Return node/relation counts and the resolver status."""
    nodes_by_kind: dict[str, int] = {}
    for node in graph.nodes.values():
        nodes_by_kind[node.kind.value] = (
            nodes_by_kind.get(node.kind.value, 0) + 1
        )
    rels_by_kind: dict[str, int] = {}
    for rel in graph.relations:
        rels_by_kind[rel.kind.value] = rels_by_kind.get(rel.kind.value, 0) + 1
    return {
        "nodes": len(graph.nodes),
        "relations": len(graph.relations),
        "nodes_by_kind": nodes_by_kind,
        "relations_by_kind": rels_by_kind,
        "resolver_status": graph.metadata.get(RESOLVER_STATUS_KEY),
    }


def find_nodes(graph: GraphLens, name: str) -> list[dict[str, object]]:
    """Return nodes whose short or qualified name matches *name*."""
    return [_node_dict(n) for n in graph.nodes_by_name(name)]


def _gather(
    graph: GraphLens, node: str, op: str, depth: int
) -> list[dict[str, object]]:
    seen: dict[str, Node] = {}
    for nid in _resolve_ids(graph, node):
        if op == "callers":
            results = graph.callers(nid)
        elif op == "callees":
            results = graph.callees(nid)
        elif op == "references":
            results = graph.references_to(nid)
        else:  # neighbors
            results = graph.neighbors(nid, depth=depth)
        for n in results:
            seen[n.id] = n
    return [_node_dict(n) for n in seen.values()]


def callers(graph: GraphLens, node: str) -> list[dict[str, object]]:
    """Return functions/methods that call *node*."""
    return _gather(graph, node, "callers", 1)


def callees(graph: GraphLens, node: str) -> list[dict[str, object]]:
    """Return functions/methods that *node* calls."""
    return _gather(graph, node, "callees", 1)


def references(graph: GraphLens, node: str) -> list[dict[str, object]]:
    """Return nodes that reference *node*."""
    return _gather(graph, node, "references", 1)


def neighbors(
    graph: GraphLens, node: str, depth: int = 1
) -> list[dict[str, object]]:
    """Return nodes within *depth* hops of *node*."""
    return _gather(graph, node, "neighbors", depth)


def communicates_with(graph: GraphLens) -> list[dict[str, object]]:
    """Return cross-language ``COMMUNICATES_WITH`` edges (consumer→server)."""
    edges: list[dict[str, object]] = []
    for rel in graph.relations:
        if rel.kind is not RelationKind.COMMUNICATES_WITH:
            continue
        source = graph.nodes.get(rel.source_id)
        target = graph.nodes.get(rel.target_id)
        if source is None or target is None:
            continue  # pragma: no cover - dangling edge
        edges.append(
            {
                "consumer": source.qualified_name,
                "provider": target.qualified_name,
                "mechanism": rel.metadata.get("mechanism"),
                "key": rel.metadata.get("boundary_key"),
                "confidence": rel.metadata.get("confidence"),
            }
        )
    return edges


def boundaries(graph: GraphLens) -> list[dict[str, object]]:
    """Return all cross-language boundary contracts in the graph."""
    return [
        {
            "mechanism": n.metadata.get("mechanism"),
            "key": n.metadata.get("key"),
            "exposed_by": [
                graph.nodes[r.source_id].qualified_name
                for r in graph.incoming(n.id, RelationKind.EXPOSES)
                if r.source_id in graph.nodes
            ],
            "consumed_by": [
                graph.nodes[r.source_id].qualified_name
                for r in graph.incoming(n.id, RelationKind.CONSUMES)
                if r.source_id in graph.nodes
            ],
        }
        for n in graph.nodes_by_kind(NodeKind.BOUNDARY)
    ]


def _build_server(graph: GraphLens):  # noqa: ANN202  # pragma: no cover
    """Build a FastMCP server exposing the query tools (needs ``mcp``)."""
    from mcp.server.fastmcp import (  # ty: ignore[unresolved-import]
        FastMCP,
    )

    server = FastMCP("graphlens")
    server.tool(name="stats")(lambda: graph_stats(graph))
    server.tool(name="find")(lambda name: find_nodes(graph, name))
    server.tool(name="callers")(lambda node: callers(graph, node))
    server.tool(name="callees")(lambda node: callees(graph, node))
    server.tool(name="references")(lambda node: references(graph, node))
    server.tool(name="neighbors")(
        lambda node, depth=1: neighbors(graph, node, depth)
    )
    server.tool(name="communicates_with")(
        lambda: communicates_with(graph)
    )
    server.tool(name="boundaries")(lambda: boundaries(graph))
    return server


def serve(graph_path: Path) -> None:  # pragma: no cover - stdio server loop
    """Load the graph and run the MCP server over stdio."""
    _build_server(load_graph(graph_path)).run()


@app.command("mcp")
def mcp_command(
    graph_path: Annotated[
        Path,
        typer.Option(
            "--graph",
            "-g",
            help="Path to a graph JSON file (from `analyze --output`)",
            exists=True,
            dir_okay=False,
        ),
    ],
) -> None:
    """Serve the graph query API to agents over MCP (stdio)."""
    try:
        import mcp  # noqa: F401  # ty: ignore[unresolved-import]
    except ImportError as exc:
        typer.echo(
            "MCP support requires the 'mcp' package. Install it with:\n"
            "  pip install 'graphlens-cli[mcp]'",
            err=True,
        )
        raise typer.Exit(code=1) from exc
    serve(graph_path)  # pragma: no cover - requires mcp
