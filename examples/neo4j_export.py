r"""Export a graphlens code graph to Neo4j.

Usage:
    uv run python examples/neo4j_export.py <project_root> \
        [--lang python|typescript] \
        [--uri bolt://localhost:7687] \
        [--user neo4j] [--password secret] \
        [--wipe] [--batch-size 500]

Requirements:
    pip install neo4j          # or: uv add neo4j

A running Neo4j instance is required.  Quickstart with Docker:

    docker run --rm -p 7474:7474 -p 7687:7687 \
        -e NEO4J_AUTH=neo4j/password neo4j:5

Then open http://localhost:7474 and run example queries such as:

    // Top callers — functions that call the most other functions
    MATCH (src)-[:CALLS]->()
    RETURN src.qualified_name, count(*) AS calls
    ORDER BY calls DESC LIMIT 20

    // Inheritance chain
    MATCH path = (:Class)-[:INHERITS_FROM*]->(:Class)
    RETURN path LIMIT 25

    // Third-party imports used most
    MATCH (i:Import {meta_origin: "third_party"})
    RETURN i.name, count(*) AS uses ORDER BY uses DESC LIMIT 20

    // All callers of a specific function
    MATCH (caller)-[:CALLS]->(fn:Function {name: "my_func"})
    RETURN caller.qualified_name
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j import Driver


# ---------------------------------------------------------------------------
# Node / relation serialisation
# ---------------------------------------------------------------------------

_SIMPLE_TYPES = (str, int, float, bool)


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
    """Flatten a graphlens Relation's metadata into Neo4j-safe properties."""
    return {
        f"meta_{k}": v
        for k, v in rel.metadata.items()
        if isinstance(v, _SIMPLE_TYPES)
    }


def _node_label(node: Any) -> str:
    """Map NodeKind to a Neo4j PascalCase label, e.g. 'external_symbol' → 'ExternalSymbol'."""
    return node.kind.value.replace("_", " ").title().replace(" ", "")


def _batches(items: list[Any], size: int) -> Iterator[list[Any]]:
    """Yield successive *size*-length slices of *items*."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


# ---------------------------------------------------------------------------
# Cypher templates
# ---------------------------------------------------------------------------

_CONSTRAINT_CYPHER = """\
CREATE CONSTRAINT code_node_id IF NOT EXISTS
FOR (n:Code) REQUIRE n.id IS UNIQUE
"""

_WIPE_CYPHER = "MATCH (n:Code) DETACH DELETE n"

# Note: {label} is an extra label applied alongside :Code.
# Using SET n:{label} is safe because label names come from NodeKind enum values.
_NODE_CYPHER = """\
UNWIND $batch AS props
MERGE (n:Code {{id: props.id}})
SET n += props, n:{label}
"""

# Relationship type comes from RelationKind enum — no user input involved.
_REL_CYPHER = """\
UNWIND $batch AS row
MATCH (src:Code {{id: row.source_id}})
MATCH (dst:Code {{id: row.target_id}})
MERGE (src)-[r:{rel_type}]->(dst)
SET r += row.props
"""


# ---------------------------------------------------------------------------
# Import functions
# ---------------------------------------------------------------------------

def _import_nodes(
    driver: Driver, nodes: list[Any], batch_size: int
) -> int:
    """Merge all nodes into Neo4j; return count imported."""
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
    """Merge all relations into Neo4j; return count imported."""
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
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a graphlens code graph to Neo4j.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "project_root",
        help="Root directory of the project to analyse",
    )
    parser.add_argument(
        "--lang",
        default="python",
        choices=["python", "typescript"],
        help="Language adapter to use (default: python)",
    )
    parser.add_argument(
        "--uri",
        default="bolt://localhost:7687",
        help="Neo4j Bolt URI (default: bolt://localhost:7687)",
    )
    parser.add_argument("--user", default="neo4j", help="Neo4j username")
    parser.add_argument("--password", default="password", help="Neo4j password")
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="Delete all :Code nodes before importing",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        metavar="N",
        help="Nodes/relations per Cypher batch (default: 500)",
    )
    args = parser.parse_args()

    # Check neo4j driver early
    try:
        from neo4j import GraphDatabase  # noqa: PLC0415
    except ImportError:
        print(
            "neo4j driver not found.  Install it with:\n"
            "    pip install neo4j\n"
            "or inside the uv workspace:\n"
            "    uv add neo4j",
            file=sys.stderr,
        )
        sys.exit(1)

    root = Path(args.project_root).resolve()
    if not root.exists():
        print(f"error: project root not found: {root}", file=sys.stderr)
        sys.exit(1)

    # --- analyse ------------------------------------------------------------
    print(f"Analysing {root} (lang={args.lang}) …")
    t0 = time.monotonic()

    if args.lang == "python":
        from graphlens_python import PythonAdapter  # noqa: PLC0415
        graph = PythonAdapter().analyze(root)
    else:
        from graphlens_typescript import TypescriptAdapter  # noqa: PLC0415
        graph = TypescriptAdapter().analyze(root)

    elapsed = time.monotonic() - t0
    print(
        f"  {len(graph.nodes)} nodes, {len(graph.relations)} relations"
        f"  ({elapsed:.2f}s)\n"
    )

    # --- connect ------------------------------------------------------------
    print(f"Connecting to {args.uri} …")
    driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
    try:
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot connect to Neo4j: {exc}", file=sys.stderr)
        driver.close()
        sys.exit(1)

    with driver.session() as session:
        session.run(_CONSTRAINT_CYPHER)
        if args.wipe:
            result = session.run(_WIPE_CYPHER)
            deleted = result.consume().counters.nodes_deleted
            print(f"  wiped {deleted} existing :Code nodes\n")

    # --- import nodes -------------------------------------------------------
    print("Importing nodes …")
    t1 = time.monotonic()
    n_nodes = _import_nodes(driver, list(graph.nodes.values()), args.batch_size)
    print(f"  {n_nodes} nodes  ({time.monotonic() - t1:.2f}s)")

    # --- import relations ---------------------------------------------------
    print("Importing relations …")
    t2 = time.monotonic()
    n_rels = _import_relations(driver, graph.relations, args.batch_size)
    print(f"  {n_rels} relations  ({time.monotonic() - t2:.2f}s)")

    driver.close()
    print(f"\nDone.  Total time: {time.monotonic() - t0:.2f}s")
    print(
        "\nExample queries (run in Neo4j Browser at http://localhost:7474):\n"
        "\n"
        "  // Top callers\n"
        "  MATCH (src)-[:CALLS]->()\n"
        "  RETURN src.qualified_name, count(*) AS calls\n"
        "  ORDER BY calls DESC LIMIT 20\n"
        "\n"
        "  // Inheritance chain\n"
        "  MATCH path = (:Class)-[:INHERITS_FROM*]->(:Class)\n"
        "  RETURN path LIMIT 25\n"
        "\n"
        "  // Third-party imports\n"
        '  MATCH (i:Import {meta_origin: "third_party"})\n'
        "  RETURN i.name, count(*) AS uses ORDER BY uses DESC LIMIT 20\n"
    )


if __name__ == "__main__":
    main()
