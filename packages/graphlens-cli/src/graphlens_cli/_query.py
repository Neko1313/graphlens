"""graphlens query — query a previously serialized graph JSON."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from graphlens import GraphLens

from graphlens_cli._app import app

if TYPE_CHECKING:
    from collections.abc import Callable

    from graphlens import Node

_OPERATIONS = ("callers", "callees", "references", "neighbors")


def _resolve_ids(graph: GraphLens, node: str) -> list[str]:
    """Resolve *node* (an id or a qualified/short name) to node ids."""
    if node in graph.nodes:
        return [node]
    return [n.id for n in graph.nodes_by_name(node)]


@app.command()
def query(
    node: Annotated[
        str,
        typer.Argument(help="Node id, qualified name, or short name"),
    ],
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
    operation: Annotated[
        str,
        typer.Option(
            "--op",
            help="callers | callees | references | neighbors",
        ),
    ] = "callers",
    depth: Annotated[
        int,
        typer.Option(help="Hop depth for the 'neighbors' operation"),
    ] = 1,
) -> None:
    """Query a saved graph for relationships of a node."""
    if operation not in _OPERATIONS:
        msg = f"operation must be one of {', '.join(_OPERATIONS)}"
        raise typer.BadParameter(msg)

    graph = GraphLens.from_json(graph_path.read_text(encoding="utf-8"))
    ids = _resolve_ids(graph, node)
    if not ids:
        typer.echo(f"No node matching {node!r}", err=True)
        raise typer.Exit(code=1)

    ops: dict[str, Callable[[str], list[Node]]] = {
        "callers": graph.callers,
        "callees": graph.callees,
        "references": graph.references_to,
    }
    for nid in ids:
        src = graph.nodes[nid]
        results = (
            graph.neighbors(nid, depth=depth)
            if operation == "neighbors"
            else ops[operation](nid)
        )
        typer.echo(f"{operation} of {src.qualified_name} ({src.kind.value}):")
        for r in results:
            typer.echo(f"  {r.kind.value:<16} {r.qualified_name}")
        if not results:
            typer.echo("  (none)")
