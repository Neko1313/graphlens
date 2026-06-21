"""graphlens analyze — print graph statistics or serialize to JSON."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from graphlens import RESOLVER_STATUS_KEY, GraphLens, NodeKind, RelationKind

from graphlens_cli._app import app, resolve_langs, run_analysis


def _print_stats(graph: GraphLens, elapsed: float) -> None:
    """Print node/relation/external/caller statistics for *graph*."""
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
    output_format: Annotated[
        str,
        typer.Option(
            "--format",
            "-f",
            help="Output format: text (stats) or json (serialized graph)",
        ),
    ] = "text",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write the serialized graph (JSON) to this path",
            dir_okay=False,
            writable=True,
        ),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Exit non-zero if the resolver status is not 'ok'",
        ),
    ] = False,
) -> None:
    """Analyse a project: print stats, or serialize the graph to JSON."""
    quiet = output_format == "json" and output is None
    langs = resolve_langs(lang, root)
    if not quiet:
        typer.echo(f"Analysing {root}  [lang={', '.join(langs)}]\n")

    graph, elapsed = run_analysis(root, langs, verbose=not quiet)

    if output is not None:
        output.write_text(graph.to_json(indent=2), encoding="utf-8")
        typer.echo(
            f"Wrote graph JSON to {output}  "
            f"({len(graph.nodes)} nodes, {len(graph.relations)} relations)"
        )
    elif output_format == "json":
        typer.echo(graph.to_json(indent=2))
    else:
        _print_stats(graph, elapsed)

    status = str(graph.metadata.get(RESOLVER_STATUS_KEY, "ok"))
    if not quiet and status != "ok":
        typer.echo(f"\nresolver status: {status}", err=True)
    if strict and status != "ok":
        raise typer.Exit(code=1)
