"""Demo: cross-language boundary linking (Python server <-> TS client).

Analyzes a tiny FastAPI service and a tiny TypeScript frontend, merges
their graphs, runs the cross-language linker, and prints the resulting
``COMMUNICATES_WITH`` edges — each one a frontend function wired to the
backend handler that serves the route it calls.

Boundary extraction does not need the type-aware resolvers, so this works
even if ``ty`` / Node are unavailable (the structural+boundary graph is
still produced).

Run:  python examples/demo_cross_language.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from graphlens import NodeKind, RelationKind, adapter_registry
from graphlens_link import link_graph

PY_SERVER = '''\
from fastapi import FastAPI

app = FastAPI()


@app.get("/users/{user_id}")
def get_user(user_id: int):
    return {"id": user_id}


@app.post("/users")
def create_user():
    return {}
'''

TS_CLIENT = """\
export async function loadUser(id: number) {
  return fetch(`/users/${id}`);
}

export async function addUser() {
  return axios.post("/users", {});
}
"""


def _write_python_service(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        '[project]\nname = "svc"\nversion = "0.1.0"\n'
        'dependencies = ["fastapi"]\n'
    )
    pkg = root / "src" / "svc"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "api.py").write_text(PY_SERVER)


def _write_ts_frontend(root: Path) -> None:
    (root / "package.json").write_text(
        '{"name": "web", "dependencies": {"axios": "^1.0.0"}}'
    )
    src = root / "src"
    src.mkdir()
    (src / "client.ts").write_text(TS_CLIENT)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        py_root = base / "service"
        ts_root = base / "web"
        py_root.mkdir()
        ts_root.mkdir()
        _write_python_service(py_root)
        _write_ts_frontend(ts_root)

        graph = adapter_registry.load("python")().analyze(py_root)
        ts_graph = adapter_registry.load("typescript")().analyze(ts_root)
        graph.merge(ts_graph, allow_shared=True)

        result = link_graph(graph)

        boundaries = graph.nodes_by_kind(NodeKind.BOUNDARY)
        print(f"BOUNDARY nodes:        {len(boundaries)}")
        print(f"linked boundaries:     {result.boundaries_linked}")
        print(f"COMMUNICATES_WITH:     {result.relations_added}\n")

        for boundary in boundaries:
            print(f"  [{boundary.metadata['mechanism']}] {boundary.name}")

        print("\nCross-language calls (client -> server):")
        for rel in graph.relations:
            if rel.kind is not RelationKind.COMMUNICATES_WITH:
                continue
            src = graph.nodes[rel.source_id]
            dst = graph.nodes[rel.target_id]
            key = rel.metadata["boundary_key"]
            print(f"  {src.qualified_name}  --[{key}]-->  {dst.qualified_name}")


if __name__ == "__main__":
    main()
