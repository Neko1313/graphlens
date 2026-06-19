"""Demo: analyze a Python project and show the RESOLVED graph.

Usage:
    uv run python examples/demo_resolved_graph.py <project_root> [symbol]

Prints node/relation counts, external-symbol origins, a sample of CALLS
edges resolved to real FUNCTION/METHOD nodes, and a find-usages report
(who calls the most-called internal function, or [symbol] if given).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from graphlens import NodeKind, RelationKind
from graphlens_python import PythonAdapter


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)

    root = Path(sys.argv[1]).resolve()
    want = sys.argv[2] if len(sys.argv) > 2 else None

    graph = PythonAdapter().analyze(root)
    nodes = graph.nodes

    print(f"\n=== {root} ===")
    print(f"nodes: {len(nodes)}   relations: {len(graph.relations)}\n")

    by_kind = Counter(n.kind.value for n in nodes.values())
    print("nodes by kind:")
    for k, c in by_kind.most_common():
        print(f"  {k:16} {c}")

    rel_kind = Counter(r.kind.value for r in graph.relations)
    print("\nrelations by kind:")
    for k, c in rel_kind.most_common():
        print(f"  {k:16} {c}")

    ext_origin = Counter(
        str(n.metadata.get("origin", "?"))
        for n in nodes.values()
        if n.kind == NodeKind.EXTERNAL_SYMBOL
    )
    if ext_origin:
        print("\nexternal symbols by origin (stdlib vs third_party vs ...):")
        for o, c in ext_origin.most_common():
            print(f"  {o:16} {c}")

    # CALLS resolved to real FUNCTION/METHOD nodes — the whole point.
    calls = [r for r in graph.relations if r.kind == RelationKind.CALLS]
    resolved = [
        r
        for r in calls
        if r.target_id in nodes
        and nodes[r.target_id].kind in (NodeKind.FUNCTION, NodeKind.METHOD)
    ]
    print(
        f"\nCALLS: {len(calls)} total, {len(resolved)} resolved to a real "
        f"FUNCTION/METHOD node (rest are stdlib/third-party/unresolved):"
    )
    for r in resolved[:12]:
        src = nodes.get(r.source_id)
        dst = nodes[r.target_id]
        src_name = src.qualified_name if src else r.source_id
        print(f"  {src_name}  --calls-->  {dst.qualified_name}")

    # find-usages: who USES X — both CALLS (direct calls) and REFERENCES
    # (used as a value: Depends(X), callbacks, decorator args). X = [symbol]
    # arg, else the most-called function.
    usage_kinds = (RelationKind.CALLS, RelationKind.REFERENCES)
    incoming: dict[str, list[tuple[str, str]]] = {}
    for r in graph.relations:
        if r.kind in usage_kinds:
            incoming.setdefault(r.target_id, []).append(
                (r.source_id, r.kind.value)
            )

    target_id = None
    if want:
        for nid, n in nodes.items():
            if n.kind in (NodeKind.FUNCTION, NodeKind.METHOD) and (
                n.name == want or n.qualified_name == want
            ):
                target_id = nid
                break
        if target_id is None:
            print(f"\nfind-usages: no FUNCTION/METHOD named '{want}' found")
            return
    elif incoming:
        target_id = max(incoming, key=lambda k: len(incoming[k]))

    if target_id is not None:
        tgt = nodes[target_id]
        users = incoming.get(target_id, [])
        n_calls = sum(1 for _, k in users if k == "calls")
        n_refs = sum(1 for _, k in users if k == "references")
        print(
            f"\nfind-usages: '{tgt.qualified_name}' used {len(users)}x "
            f"({n_calls} calls, {n_refs} references) by:"
        )
        for sid, kind in users:
            c = nodes.get(sid)
            print(f"  [{kind:10}] {c.qualified_name if c else sid}")


if __name__ == "__main__":
    main()
