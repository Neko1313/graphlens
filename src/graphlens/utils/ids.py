"""Deterministic node ID generation."""

from __future__ import annotations

import hashlib


def make_node_id(project_name: str, qualified_name: str, kind: str) -> str:
    """
    Return a stable, deterministic node ID.

    Uses a truncated SHA-256 hex digest so the same inputs always produce
    the same ID across runs, enabling incremental updates and graph diffing.
    """
    key = f"{project_name}::{kind}::{qualified_name}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def make_boundary_id(mechanism: str, key: str) -> str:
    """
    Return a stable, *language- and project-agnostic* boundary node ID.

    A cross-language contract (an HTTP route, a gRPC method, a queue topic,
    a Temporal activity) is shared by independently analyzed projects in
    different languages.  Deriving the ID purely from ``(mechanism, key)``
    means a server in one language and a client in another emit the *same*
    boundary node, which collapses into one node when their graphs are
    merged — without any shared symbol table.
    """
    raw = f"boundary::{mechanism}::{key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
