"""graphlens neo4j — export a code graph to Neo4j."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from graphlens_cli._app import app, resolve_langs, run_analysis

if TYPE_CHECKING:
    from neo4j import Driver

# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

_SIMPLE_TYPES = (str, int, float, bool)

_CONSTRAINT_CYPHER = """\
CREATE CONSTRAINT code_node_id IF NOT EXISTS
FOR (n:Code) REQUIRE n.id IS UNIQUE
"""

_WIPE_CYPHER = "MATCH (n:Code) DETACH DELETE n"

_NODE_CYPHER = """\
UNWIND $batch AS props
MERGE (n:Code {{id: props.id}})
SET n += props, n:{label}
"""

_REL_CYPHER = """\
UNWIND $batch AS row
MATCH (src:Code {{id: row.source_id}})
MATCH (dst:Code {{id: row.target_id}})
MERGE (src)-[r:{rel_type}]->(dst)
SET r += row.props
"""


def _node_props(node: Any) -> dict[str, Any]:
    """Flatten a graphlens Node into Neo4j-safe scalar properties."""
    props: dict[str, Any] = {
        "id": node.id,
        "kind": node.kind.value,
        "name": node.name,
        "qualified_name": node.qualified_name,
    }
    if node.file_path is not None:
        props["file_path"] = node.file_path
    if node.span is not None:
        props["span_start_line"] = node.span.start_line
        props["span_start_col"] = node.span.start_col
        props["span_end_line"] = node.span.end_line
        props["span_end_col"] = node.span.end_col
    for k, v in node.metadata.items():
        if isinstance(v, _SIMPLE_TYPES):
            props[f"meta_{k}"] = v
    return props


def _rel_props(rel: Any) -> dict[str, Any]:
    """Flatten relation metadata into Neo4j-safe scalar properties."""
    return {
        f"meta_{k}": v
        for k, v in rel.metadata.items()
        if isinstance(v, _SIMPLE_TYPES)
    }


def _node_label(node: Any) -> str:
    """Map NodeKind to a PascalCase Neo4j label (e.g. ExternalSymbol)."""
    return node.kind.value.replace("_", " ").title().replace(" ", "")


def _batches(items: list[Any], size: int) -> Iterator[list[Any]]:
    """Yield successive *size*-length slices of *items*."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _import_nodes(driver: Driver, nodes: list[Any], batch_size: int) -> int:
    """Merge all *nodes* into Neo4j; return the total count written."""
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in nodes:
        by_label[_node_label(n)].append(_node_props(n))

    total = 0
    with driver.session() as session:
        for lbl, props_list in by_label.items():
            cypher = _NODE_CYPHER.format(label=lbl)
            for batch in _batches(props_list, batch_size):
                session.run(cypher, batch=batch)
                total += len(batch)
    return total


def _import_relations(
    driver: Driver, relations: list[Any], batch_size: int
) -> int:
    """Merge all *relations* into Neo4j grouped by type; return count."""
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in relations:
        by_type[r.kind.value.upper()].append({
            "source_id": r.source_id,
            "target_id": r.target_id,
            "props": _rel_props(r),
        })

    total = 0
    with driver.session() as session:
        for rel_type, rows in by_type.items():
            cypher = _REL_CYPHER.format(rel_type=rel_type)
            for batch in _batches(rows, batch_size):
                session.run(cypher, batch=batch)
                total += len(batch)
    return total


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@app.command()
def neo4j(
    root: Annotated[
        Path,
        typer.Argument(
            help="Project root to analyse",
            exists=True,
            file_okay=False,
            resolve_path=True,
        ),
    ],
    lang: Annotated[
        str,
        typer.Option(
            help="Adapter(s): auto | python | typescript | python,typescript",
            show_default=True,
        ),
    ] = "auto",
    uri: Annotated[
        str,
        typer.Option(help="Neo4j Bolt URI", show_default=True),
    ] = "bolt://localhost:7687",
    user: Annotated[
        str,
        typer.Option(help="Neo4j username", show_default=True),
    ] = "neo4j",
    password: Annotated[
        str,
        typer.Option(help="Neo4j password", show_default=True),
    ] = "password",
    wipe: Annotated[
        bool,
        typer.Option("--wipe/--no-wipe", help="Wipe :Code nodes first"),
    ] = False,
    batch_size: Annotated[
        int,
        typer.Option("--batch-size", help="Items per Cypher batch", min=1),
    ] = 500,
) -> None:
    """Export a code graph to a running Neo4j instance."""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        typer.echo(
            "neo4j driver not installed. Run:  pip install neo4j",
            err=True,
        )
        raise typer.Exit(1) from None

    langs = resolve_langs(lang, root)
    typer.echo(f"Analysing {root}  [lang={', '.join(langs)}]")
    graph, elapsed = run_analysis(root, langs)
    typer.echo(
        f"  {len(graph.nodes)} nodes, {len(graph.relations)} relations"
        f"  ({elapsed:.2f}s)\n"
    )

    typer.echo(f"Connecting to {uri} …")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        driver.verify_connectivity()
    except Exception as exc:
        typer.echo(f"error: cannot connect to Neo4j: {exc}", err=True)
        driver.close()
        raise typer.Exit(1) from exc

    with driver.session() as session:
        session.run(_CONSTRAINT_CYPHER)
        if wipe:
            result = session.run(_WIPE_CYPHER)
            deleted = result.consume().counters.nodes_deleted
            typer.echo(f"  wiped {deleted} existing :Code nodes\n")

    import time as _time

    typer.echo("Importing nodes …")
    t1 = _time.monotonic()
    n_nodes = _import_nodes(driver, list(graph.nodes.values()), batch_size)
    typer.echo(f"  {n_nodes} nodes  ({_time.monotonic() - t1:.2f}s)")

    typer.echo("Importing relations …")
    t2 = _time.monotonic()
    n_rels = _import_relations(driver, graph.relations, batch_size)
    typer.echo(f"  {n_rels} relations  ({_time.monotonic() - t2:.2f}s)")

    driver.close()
    typer.echo("\nDone.  Explore at http://localhost:7474")
