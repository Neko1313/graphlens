"""
Cross-language boundary extractors for TypeScript (TCK-6).

Mirrors the Python extractors: each returns language-agnostic
:class:`BoundaryRef` ports.  Path keys are normalized through the shared
``graphlens.normalize_http_path`` so a TS ``fetch("/users/1")`` and a
Python ``@app.get("/users/{id}")`` land on the same ``BOUNDARY`` node.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from graphlens import BoundaryRef, normalize_http_path

from graphlens_typescript._queries import run_query

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_HTTP_VERBS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)
_SERVER_OBJECTS = frozenset({"app", "router", "server"})
_CLIENT_OBJECTS = frozenset({"axios", "http", "https"})

# ``app.get("/x", handler)`` / ``axios.post("/x", body)``.
_Q_MEMBER_CALL = """
(call_expression
  function: (member_expression
    object: (identifier) @obj
    property: (property_identifier) @method)
  arguments: (arguments) @args)
"""
# ``fetch("/x")``.
_Q_FETCH = """
(call_expression
  function: (identifier) @fn
  arguments: (arguments) @args)
"""
# ``@Get("/x")`` NestJS-style controller method decorator.
_Q_DECORATOR = """
(decorator (call_expression
  function: (identifier) @deco
  arguments: (arguments) @args))
"""


def _text(node: TSNode | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _pos(node: TSNode) -> tuple[int, int]:
    """Return the 1-based ``(line, col)`` of a node's start."""
    return node.start_point[0] + 1, node.start_point[1] + 1


def _url_template(node: TSNode) -> str | None:
    """Reduce a string / template literal to a path template, else None."""
    if node.type == "string":
        for child in node.children:
            if child.type == "string_fragment":
                return _text(child)
        return ""
    if node.type == "template_string":
        parts: list[str] = []
        for child in node.children:
            if child.type == "string_fragment":
                parts.append(_text(child))
            elif child.type == "template_substitution":
                parts.append("{}")
        return "".join(parts)
    return None


def _first_url(args: TSNode) -> str | None:
    """Return the normalized first argument if it is a string/template."""
    kids = args.named_children
    if not kids:
        return None
    return _url_template(kids[0])


def _string_literal(node: TSNode) -> str | None:
    """Return the content of a plain ``"..."`` string literal, else None."""
    if node.type != "string":
        return None
    for child in node.children:
        if child.type == "string_fragment":
            return _text(child)
    return ""


def _fetch_method(args: TSNode) -> str:
    """
    Return the HTTP method from ``fetch(url, {method: "..."})``.

    Defaults to ``GET`` when there is no options object or the ``method``
    value is not a string literal (so a non-GET fetch is keyed correctly
    instead of always being recorded as a GET boundary).
    """
    kids = args.named_children
    if len(kids) < 2 or kids[1].type != "object":  # noqa: PLR2004
        return "GET"
    for pair in kids[1].named_children:
        if pair.type != "pair":
            continue
        key = pair.child_by_field_name("key")
        if key is None:
            continue
        key_name = _string_literal(key) if key.type == "string" else _text(key)
        if key_name != "method":
            continue
        value = pair.child_by_field_name("value")
        method = _string_literal(value) if value is not None else None
        if method:
            return method.upper()
    return "GET"


def _is_http_path(url: str) -> bool:
    """Return True if *url* looks like a request path or absolute URL."""
    return url.startswith("/") or "://" in url


class TsBoundaryExtractor(ABC):
    """Recognizes one boundary mechanism in a parsed TypeScript file."""

    @abstractmethod
    def mechanism(self) -> str:
        """Return the boundary family this extractor emits."""
        ...

    @abstractmethod
    def extract(self, root: TSNode, lang: str) -> list[BoundaryRef]:
        """Return every boundary port found under ``root``."""
        ...


def _http_ref(role: str, verb: str, url: str, node: TSNode) -> BoundaryRef:
    norm = normalize_http_path(url)
    line, col = _pos(node)
    confidence = 1.0 if role == "server" else 0.9
    return BoundaryRef(
        mechanism="http",
        role=role,
        key=f"{verb} {norm}",
        line=line,
        col=col,
        confidence=confidence,
        detail={"method": verb, "path": norm},
    )


class HttpServerExtractor(TsBoundaryExtractor):
    """Express ``app.get(...)`` routes and NestJS ``@Get(...)`` methods."""

    def mechanism(self) -> str:
        return "http"

    def extract(self, root: TSNode, lang: str) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_MEMBER_CALL, root, lang):
            if _text(caps["obj"][0]) not in _SERVER_OBJECTS:
                continue
            method = _text(caps["method"][0]).lower()
            if method not in _HTTP_VERBS:
                continue
            url = _first_url(caps["args"][0])
            # Express routes are absolute paths; a non-path string is an
            # ``app.get("view engine")`` settings getter, not a route.
            if url is None or not _is_http_path(url):
                continue
            refs.append(
                _http_ref("server", method.upper(), url, caps["method"][0])
            )
        for caps in run_query(_Q_DECORATOR, root, lang):
            method = _text(caps["deco"][0]).lower()
            if method not in _HTTP_VERBS:
                continue
            url = _first_url(caps["args"][0])
            # NestJS decorator paths may be relative (``@Get("users")``) but
            # an empty string is never a real route.
            if not url:
                continue
            refs.append(
                _http_ref("server", method.upper(), url, caps["deco"][0])
            )
        return refs


class HttpClientExtractor(TsBoundaryExtractor):
    """``fetch(...)`` and ``axios.get(...)`` client calls."""

    def mechanism(self) -> str:
        return "http"

    def extract(self, root: TSNode, lang: str) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_FETCH, root, lang):
            if _text(caps["fn"][0]) != "fetch":
                continue
            url = _first_url(caps["args"][0])
            if url is None or not _is_http_path(url):
                continue
            method = _fetch_method(caps["args"][0])
            refs.append(_http_ref("client", method, url, caps["fn"][0]))
        for caps in run_query(_Q_MEMBER_CALL, root, lang):
            if _text(caps["obj"][0]) not in _CLIENT_OBJECTS:
                continue
            method = _text(caps["method"][0]).lower()
            if method not in _HTTP_VERBS:
                continue
            url = _first_url(caps["args"][0])
            if url is None or not _is_http_path(url):
                continue
            refs.append(
                _http_ref("client", method.upper(), url, caps["method"][0])
            )
        return refs


def _queue_role(method: str) -> str | None:
    """Map a queue method name to a boundary role, else None."""
    if method in ("publish", "produce", "emit"):
        return "client"  # a producer invokes the topic
    if method == "subscribe":
        return "server"  # a consumer handles the topic
    return None


class QueueExtractor(TsBoundaryExtractor):
    """Message-queue producers (publish/produce/emit) and consumers."""

    def mechanism(self) -> str:
        return "queue"

    def extract(self, root: TSNode, lang: str) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_MEMBER_CALL, root, lang):
            role = _queue_role(_text(caps["method"][0]).lower())
            if role is None:
                continue
            topic = _first_url(caps["args"][0])
            if topic is None:
                continue
            line, col = _pos(caps["method"][0])
            refs.append(
                BoundaryRef(
                    mechanism="queue",
                    role=role,
                    key=topic,
                    line=line,
                    col=col,
                    confidence=0.75 if role == "server" else 0.7,
                    detail={"topic": topic},
                )
            )
        return refs


TYPESCRIPT_DEFAULT_BOUNDARY_EXTRACTORS: list[TsBoundaryExtractor] = [
    HttpServerExtractor(),
    HttpClientExtractor(),
    QueueExtractor(),
]
