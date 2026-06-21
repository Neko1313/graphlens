"""
Cross-language boundary extractors for Python (TCK-6).

Each extractor recognizes one boundary *mechanism* in Python source and
returns language-agnostic :class:`BoundaryRef` ports (server = exposes,
client = consumes).  The adapter turns each ref into a ``BOUNDARY`` node
plus an ``EXPOSES`` / ``CONSUMES`` edge from the enclosing function, so a
Python server and, say, a TypeScript client meet at the same node.

Patterns are expressed as declarative tree-sitter queries (see
``_queries.run_query``) rather than hand-written visitor branches.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from graphlens import BoundaryRef, normalize_http_path

from graphlens_python._queries import run_query

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_HTTP_VERBS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)
_TEMPORAL_EXEC = frozenset(
    {
        "execute_activity",
        "execute_local_activity",
        "start_activity",
        "execute_activity_method",
    }
)

# A function-call decorator: ``@app.get("/x")`` / ``@activity.defn(name=...)``.
_Q_DECORATOR_CALL = """
(decorated_definition
  (decorator (call
    function: (attribute) @deco
    arguments: (argument_list) @args))
  definition: (function_definition name: (identifier) @func))
"""
# A bare attribute decorator: ``@activity.defn``.
_Q_DECORATOR_BARE = """
(decorated_definition
  (decorator (attribute) @deco)
  definition: (function_definition name: (identifier) @func))
"""
# An attribute call: ``requests.get("/x")`` / ``workflow.execute_activity(a)``.
_Q_ATTR_CALL = """
(call
  function: (attribute attribute: (identifier) @method)
  arguments: (argument_list) @args) @call
"""


def _text(node: TSNode | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _pos(node: TSNode) -> tuple[int, int]:
    """Return the 1-based ``(line, col)`` of a node's start."""
    return node.start_point[0] + 1, node.start_point[1] + 1


def _string_template(node: TSNode) -> str:
    """
    Reduce a Python string/f-string node to a normalized template.

    ``"/users/{id}"`` and ``f"/users/{id}"`` both become ``/users/{id}``;
    interpolations collapse to ``{}`` so a literal and an f-string that
    target the same route line up.
    """
    parts: list[str] = []
    for child in node.children:
        if child.type == "string_content":
            parts.append(_text(child))
        elif child.type == "interpolation":
            parts.append("{}")
    if parts:
        return "".join(parts)
    return _text(node).strip("'\"")


def _first_positional(args: TSNode) -> TSNode | None:
    """Return the first positional (non-keyword) argument node."""
    for child in args.named_children:
        if child.type == "keyword_argument":
            return None
        return child
    return None


def _first_string(args: TSNode) -> TSNode | None:
    """Return the first *positional* string argument, if any."""
    first = _first_positional(args)
    if first is not None and first.type == "string":
        return first
    return None


def _methods_kwarg(args: TSNode) -> list[str]:
    """Parse a Flask ``methods=[...]`` kwarg; default ``["GET"]``."""
    for child in args.named_children:
        if child.type != "keyword_argument":
            continue
        name = child.child_by_field_name("name")
        value = child.child_by_field_name("value")
        if name is None or value is None or _text(name) != "methods":
            continue
        verbs = [
            _string_template(s).upper()
            for s in value.named_children
            if s.type == "string"
        ]
        if verbs:
            return verbs
    return ["GET"]


class PyBoundaryExtractor(ABC):
    """Recognizes one boundary mechanism in a parsed Python file."""

    @abstractmethod
    def mechanism(self) -> str:
        """Return the boundary family this extractor emits."""
        ...

    @abstractmethod
    def extract(self, root: TSNode) -> list[BoundaryRef]:
        """Return every boundary port found under ``root``."""
        ...


class HttpServerExtractor(PyBoundaryExtractor):
    """FastAPI / Flask / Starlette route decorators (server side)."""

    def mechanism(self) -> str:
        return "http"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_DECORATOR_CALL, root):
            args = caps["args"][0]
            method = _text(caps["deco"][0]).rsplit(".", 1)[-1].lower()
            path_node = _first_string(args)
            if path_node is None:
                continue
            path = _string_template(path_node)
            line, col = _pos(caps["func"][0])
            if method in _HTTP_VERBS:
                refs.append(self._ref(method.upper(), path, line, col))
            elif method == "route":
                refs.extend(
                    self._ref(verb, path, line, col)
                    for verb in _methods_kwarg(args)
                )
        return refs

    def _ref(
        self, verb: str, path: str, line: int, col: int
    ) -> BoundaryRef:
        norm = normalize_http_path(path)
        return BoundaryRef(
            mechanism="http",
            role="server",
            key=f"{verb} {norm}",
            line=line,
            col=col,
            confidence=1.0,
            detail={"method": verb, "path": norm},
        )


class HttpClientExtractor(PyBoundaryExtractor):
    """``requests`` / ``httpx`` / session client calls (client side)."""

    def mechanism(self) -> str:
        return "http"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_ATTR_CALL, root):
            call = caps["call"][0]
            if call.parent is not None and call.parent.type == "decorator":
                continue  # a route decorator, not a client call
            method = _text(caps["method"][0]).lower()
            if method not in _HTTP_VERBS:
                continue
            url_node = _first_string(caps["args"][0])
            if url_node is None:
                continue
            url = _string_template(url_node)
            if not url.startswith("/") and "://" not in url:
                continue  # not a URL/path (e.g. dict.get("key"))
            confidence = 0.9 if url.startswith("/") else 0.8
            norm = normalize_http_path(url)
            line, col = _pos(caps["method"][0])
            refs.append(
                BoundaryRef(
                    mechanism="http",
                    role="client",
                    key=f"{method.upper()} {norm}",
                    line=line,
                    col=col,
                    confidence=confidence,
                    detail={"method": method.upper(), "path": norm},
                )
            )
        return refs


class TemporalExtractor(PyBoundaryExtractor):
    """Temporal / DBOS activities: ``@activity.defn`` and execute calls."""

    def mechanism(self) -> str:
        return "temporal"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        return [*self._servers(root), *self._clients(root)]

    def _servers(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_DECORATOR_BARE, root):
            func = caps["func"][0]
            if not self._is_defn(_text(caps["deco"][0])):
                continue
            refs.append(self._server_ref(_text(func), func))
        for caps in run_query(_Q_DECORATOR_CALL, root):
            func = caps["func"][0]
            if not self._is_defn(_text(caps["deco"][0])):
                continue
            name = self._defn_name(caps["args"][0]) or _text(func)
            refs.append(self._server_ref(name, func))
        return refs

    def _clients(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_ATTR_CALL, root):
            method_node = caps["method"][0]
            if _text(method_node) not in _TEMPORAL_EXEC:
                continue
            name = self._exec_target(caps["args"][0])
            if not name:
                continue
            line, col = _pos(method_node)
            refs.append(
                BoundaryRef(
                    mechanism="temporal",
                    role="client",
                    key=name,
                    line=line,
                    col=col,
                    confidence=0.9,
                    detail={"activity": name},
                )
            )
        return refs

    @staticmethod
    def _is_defn(deco_text: str) -> bool:
        return deco_text.startswith("activity.") and deco_text.endswith(
            ".defn"
        )

    @staticmethod
    def _defn_name(args: TSNode) -> str | None:
        for child in args.named_children:
            if child.type != "keyword_argument":
                continue
            name = child.child_by_field_name("name")
            value = child.child_by_field_name("value")
            if (
                name is not None
                and value is not None
                and _text(name) == "name"
                and value.type == "string"
            ):
                return _string_template(value)
        return None

    @staticmethod
    def _exec_target(args: TSNode) -> str | None:
        first = _first_positional(args)
        if first is None:
            return None
        if first.type == "string":
            return _string_template(first)
        if first.type == "identifier":
            return _text(first)
        if first.type == "attribute":
            return _text(first).rsplit(".", 1)[-1]
        return None

    def _server_ref(self, name: str, func: TSNode) -> BoundaryRef:
        line, col = _pos(func)
        return BoundaryRef(
            mechanism="temporal",
            role="server",
            key=name,
            line=line,
            col=col,
            confidence=1.0,
            detail={"activity": name},
        )


def _queue_role(method: str) -> str | None:
    """Map a queue method name to a boundary role, else None."""
    if method in ("publish", "produce"):
        return "client"  # a producer invokes the topic
    if method == "subscribe":
        return "server"  # a consumer handles the topic
    return None


class QueueExtractor(PyBoundaryExtractor):
    """Message-queue producers (publish/produce) and consumers (subscribe)."""

    def mechanism(self) -> str:
        return "queue"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_ATTR_CALL, root):
            role = _queue_role(_text(caps["method"][0]).lower())
            if role is None:
                continue
            topic_node = _first_string(caps["args"][0])
            if topic_node is None:
                continue
            topic = _string_template(topic_node)
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


PY_DEFAULT_BOUNDARY_EXTRACTORS: list[PyBoundaryExtractor] = [
    HttpServerExtractor(),
    HttpClientExtractor(),
    TemporalExtractor(),
    QueueExtractor(),
]
