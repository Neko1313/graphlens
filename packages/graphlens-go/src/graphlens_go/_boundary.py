"""
Cross-language boundary extractors for Go (TCK-6).

Server: router method routes ``r.GET("/x", h)`` (gin / chi / echo, any
verb-named method on a non-``http`` receiver).  Client: ``http.Get(url)``
and friends.  Keys go through the shared ``normalize_http_path`` so a Go
backend route lines up with a TS/Python client call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from graphlens import BoundaryRef, normalize_http_path

from graphlens_go._queries import run_query

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_HTTP_VERBS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)

# ``r.GET("/x", h)`` / ``http.Get("/x")`` — a method call on a receiver.
_Q_SELECTOR_CALL = """
(call_expression
  function: (selector_expression
    operand: (identifier) @obj
    field: (field_identifier) @method)
  arguments: (argument_list) @args)
"""


def _text(node: TSNode | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _pos(node: TSNode) -> tuple[int, int]:
    """Return the 1-based ``(line, col)`` of a node's start."""
    return node.start_point[0] + 1, node.start_point[1] + 1


def _string_content(node: TSNode) -> str | None:
    """Return the content of a Go string literal, else None."""
    if node.type in ("interpreted_string_literal", "raw_string_literal"):
        for child in node.children:
            if child.type in (
                "interpreted_string_literal_content",
                "raw_string_literal_content",
            ):
                return _text(child)
        return ""
    return None


def _first_string(args: TSNode) -> str | None:
    """Return the content of the first argument if it is a string literal."""
    for child in args.named_children:
        return _string_content(child)
    return None


def _ref(role: str, verb: str, path: str, node: TSNode) -> BoundaryRef:
    norm = normalize_http_path(path)
    line, col = _pos(node)
    return BoundaryRef(
        mechanism="http",
        role=role,
        key=f"{verb} {norm}",
        line=line,
        col=col,
        confidence=1.0 if role == "server" else 0.85,
        detail={"method": verb, "path": norm},
    )


class GoBoundaryExtractor(ABC):
    """Recognizes one boundary mechanism in a parsed Go file."""

    @abstractmethod
    def mechanism(self) -> str:
        """Return the boundary family this extractor emits."""
        ...

    @abstractmethod
    def extract(self, root: TSNode) -> list[BoundaryRef]:
        """Return every boundary port found under ``root``."""
        ...


class HttpServerExtractor(GoBoundaryExtractor):
    """gin / chi / echo router method routes (``r.GET(...)``)."""

    def mechanism(self) -> str:
        return "http"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_SELECTOR_CALL, root):
            if _text(caps["obj"][0]) == "http":
                continue  # the net/http package — client side
            method = _text(caps["method"][0]).lower()
            if method not in _HTTP_VERBS:
                continue
            path = _first_string(caps["args"][0])
            if path is None:
                continue
            refs.append(
                _ref("server", method.upper(), path, caps["method"][0])
            )
        return refs


class HttpClientExtractor(GoBoundaryExtractor):
    """net/http client calls (``http.Get(url)``)."""

    def mechanism(self) -> str:
        return "http"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_SELECTOR_CALL, root):
            if _text(caps["obj"][0]) != "http":
                continue
            method = _text(caps["method"][0]).lower()
            if method not in _HTTP_VERBS:
                continue
            url = _first_string(caps["args"][0])
            if url is None or (
                not url.startswith("/") and "://" not in url
            ):
                continue
            refs.append(
                _ref("client", method.upper(), url, caps["method"][0])
            )
        return refs


GO_DEFAULT_BOUNDARY_EXTRACTORS: list[GoBoundaryExtractor] = [
    HttpServerExtractor(),
    HttpClientExtractor(),
]
