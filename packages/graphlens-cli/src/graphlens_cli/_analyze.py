"""graphlens analyze — print graph statistics."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from graphlens import NodeKind, RelationKind

from graphlens_cli._app import app, resolve_langs, run_analysis


@app.command()
def analyze(
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
            help=(
                "Adapter(s) to use: auto | python | typescript"
                " | python,typescript"
            ),
            show_default=True,
        ),
    ] = "auto",
) -> None:
    """Print node and relation statistics for a project."""
    langs = resolve_langs(lang, root)
    typer.echo(f"Analysing {root}  [lang={', '.join(langs)}]\n")

    graph, elapsed = run_analysis(root, langs)
    nodes = graph.nodes

    typer.echo(
        f"{len(nodes)} nodes  ·  "
        f"{len(graph.relations)} relations  ·  "
        f"{elapsed:.2f}s\n"
    )

    by_kind = Counter(n.kind.value for n in nodes.values())
    typer.echo("Nodes by kind:")
    for k, c in by_kind.most_common():
        typer.echo(f"  {k:<20} {c}")

    by_rel = Counter(r.kind.value for r in graph.relations)
    typer.echo("\nRelations by kind:")
    for k, c in by_rel.most_common():
        typer.echo(f"  {k:<20} {c}")

    ext_origin = Counter(
        str(n.metadata.get("origin", "?"))
        for n in nodes.values()
        if n.kind == NodeKind.EXTERNAL_SYMBOL
    )
    if ext_origin:
        typer.echo("\nExternal symbols by origin:")
        for o, c in ext_origin.most_common():
            typer.echo(f"  {o:<20} {c}")

    caller_counts: Counter[str] = Counter()
    for r in graph.relations:
        if r.kind == RelationKind.CALLS:
            caller_counts[r.source_id] += 1

    if caller_counts:
        typer.echo("\nTop callers (by outgoing CALLS):")
        for nid, count in caller_counts.most_common(10):
            n = nodes.get(nid)
            name = n.qualified_name if n else nid
            typer.echo(f"  {count:>4}  {name}")
