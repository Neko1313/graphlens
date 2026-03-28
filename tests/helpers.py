"""Shared test helpers for core library tests."""

from pathlib import Path

from graphlens import (
    GraphLens,
    LanguageAdapter,
    Node,
    NodeKind,
    Relation,
    RelationKind,
)
from graphlens.utils.ids import make_node_id
from graphlens.utils.span import Span


def make_node(
    project: str = "proj",
    qname: str = "proj.mod",
    kind: NodeKind = NodeKind.MODULE,
    name: str = "mod",
    file_path: str | None = None,
    span: Span | None = None,
    metadata: dict | None = None,
) -> Node:
    return Node(
        id=make_node_id(project, qname, kind.value),
        kind=kind,
        qualified_name=qname,
        name=name,
        file_path=file_path,
        span=span,
        metadata=metadata or {},
    )


def make_relation(
    source_id: str,
    target_id: str,
    kind: RelationKind = RelationKind.CONTAINS,
) -> Relation:
    return Relation(source_id=source_id, target_id=target_id, kind=kind)


class StubAdapter(LanguageAdapter):
    """Minimal LanguageAdapter for registry tests."""

    def language(self) -> str:
        return "stub"

    def can_handle(self, project_root: Path) -> bool:
        return False

    def analyze(self, project_root: Path, files: list[Path] | None = None) -> GraphLens:
        return GraphLens()
