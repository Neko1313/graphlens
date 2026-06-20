"""graphlens CLI — typer app and shared analysis helpers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import typer
from graphlens import GraphLens, adapter_registry

if TYPE_CHECKING:
    from pathlib import Path

    from graphlens.contracts import LanguageAdapter

app = typer.Typer(
    name="graphlens",
    help="Polyglot code graph analysis and export.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def resolve_langs(lang: str, root: Path) -> list[str]:
    """
    Expand the *lang* value to a concrete list of adapter names.

    ``"auto"`` queries the adapter registry and filters by ``can_handle``.
    Any other value is split on commas and returned as-is.
    """
    if lang != "auto":
        return [s.strip() for s in lang.split(",") if s.strip()]

    available = adapter_registry.available()
    if not available:
        msg = (
            "No graphlens adapters installed. "
            "Install graphlens-python or graphlens-typescript."
        )
        raise typer.BadParameter(msg)

    matched: list[str] = []
    for name in available:
        try:
            if adapter_registry.load(name)().can_handle(root):
                matched.append(name)
        except Exception:
            pass
    if not matched:
        msg = (
            f"No adapter can handle {root}. "
            f"Available: {available}. "
            "Use --lang to specify explicitly."
        )
        raise typer.BadParameter(msg)
    return matched


def load_adapter(lang: str) -> LanguageAdapter:
    """
    Return an initialised adapter for *lang*.

    Tries the registry first; falls back to direct import for adapters that
    may not yet be registered via entry points.
    """
    try:
        return adapter_registry.load(lang)()
    except Exception:
        pass

    if lang == "python":
        from graphlens_python import PythonAdapter

        return PythonAdapter()
    if lang == "typescript":
        from graphlens_typescript import TypescriptAdapter

        return TypescriptAdapter()

    msg = f"Unknown or unavailable adapter: {lang!r}"
    raise typer.BadParameter(msg)


def merge_graph(target: GraphLens, source: GraphLens) -> None:
    """Merge *source* into *target* in-place, skipping duplicate node IDs."""
    for nid, node in source.nodes.items():
        if nid not in target.nodes:
            target.add_node(node)
    for rel in source.relations:
        target.add_relation(rel)


def run_analysis(
    root: Path,
    langs: list[str],
    *,
    verbose: bool = True,
) -> tuple[GraphLens, float]:
    """Analyse *root* with each adapter; return merged graph and elapsed."""
    combined = GraphLens()
    t0 = time.monotonic()
    for lang in langs:
        if verbose:
            typer.echo(f"  [{lang}] analysing {root} …")
        adapter = load_adapter(lang)
        g = adapter.analyze(root)
        if verbose:
            typer.echo(
                f"  [{lang}] {len(g.nodes)} nodes,"
                f" {len(g.relations)} relations"
            )
        merge_graph(combined, g)
    return combined, time.monotonic() - t0
