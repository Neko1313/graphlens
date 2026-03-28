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
