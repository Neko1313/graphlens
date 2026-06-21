"""graphlens-cli — command-line interface for graphlens code analysis."""

import graphlens_cli._analyze
import graphlens_cli._mcp
import graphlens_cli._neo4j
import graphlens_cli._query
import graphlens_cli._visualize  # noqa: F401  — registers visualize command
from graphlens_cli._app import app

__all__ = ["app"]
