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


_TEMPORAL_EXEC = frozenset(
    {"executeactivity", "executelocalactivity"}
)

# gRPC: protoc generates ``New<Service>Client`` constructors.
_Q_GRPC_CLIENT_ASSIGN = """
(short_var_declaration
  left: (expression_list (identifier) @var)
  right: (expression_list (call_expression
    function: (selector_expression field: (field_identifier) @callee))))
"""
_GRPC_NEW = "New"
_GRPC_CLIENT = "Client"


def _grpc_client_service(callee: str) -> str | None:
    """Return the service from a ``New<Service>Client`` constructor name."""
    if (
        callee.startswith(_GRPC_NEW)
        and callee.endswith(_GRPC_CLIENT)
        and len(callee) > len(_GRPC_NEW) + len(_GRPC_CLIENT)
    ):
        return callee[len(_GRPC_NEW) : -len(_GRPC_CLIENT)]
    return None


def _grpc_ref(service: str, method: str, node: TSNode) -> BoundaryRef:
    line, col = _pos(node)
    return BoundaryRef(
        mechanism="grpc",
        role="client",
        key=f"{service}/{method}",
        line=line,
        col=col,
        confidence=0.85,
        detail={"service": service, "method": method},
    )


class GrpcExtractor(GoBoundaryExtractor):
    """gRPC client calls on a ``New<Service>Client`` stub (client side)."""

    def mechanism(self) -> str:
        return "grpc"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        clients: dict[str, str] = {}
        for caps in run_query(_Q_GRPC_CLIENT_ASSIGN, root):
            service = _grpc_client_service(_text(caps["callee"][0]))
            if service is not None:
                clients[_text(caps["var"][0])] = service
        return [
            _grpc_ref(service, _text(caps["method"][0]), caps["method"][0])
            for caps in run_query(_Q_SELECTOR_CALL, root)
            if (service := clients.get(_text(caps["obj"][0]))) is not None
        ]


def _activity_name(args: TSNode, index: int) -> str | None:
    """Return the activity name from the index-th positional argument."""
    kids = args.named_children
    if index >= len(kids):
        return None
    node = kids[index]
    if node.type in ("interpreted_string_literal", "raw_string_literal"):
        return _string_content(node)
    if node.type == "identifier":
        return _text(node)
    if node.type == "selector_expression":
        return _text(node).rsplit(".", 1)[-1]
    return None


def _temporal_ref(role: str, name: str, node: TSNode) -> BoundaryRef:
    line, col = _pos(node)
    return BoundaryRef(
        mechanism="temporal",
        role=role,
        key=name,
        line=line,
        col=col,
        confidence=0.9,
        detail={"activity": name},
    )


class TemporalExtractor(GoBoundaryExtractor):
    """Temporal: ExecuteActivity (client) and RegisterActivity (server)."""

    def mechanism(self) -> str:
        return "temporal"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_SELECTOR_CALL, root):
            method = _text(caps["method"][0]).lower()
            args = caps["args"][0]
            node = caps["method"][0]
            if method in _TEMPORAL_EXEC:
                name = _activity_name(args, 1)  # after ctx
                if name is not None:
                    refs.append(_temporal_ref("client", name, node))
            elif method == "registeractivity":
                name = _activity_name(args, 0)
                if name is not None:
                    refs.append(_temporal_ref("server", name, node))
        return refs


def _queue_role(method: str) -> str | None:
    """Map a queue method name to a boundary role, else None."""
    if method in ("publish", "produce"):
        return "client"  # a producer invokes the topic
    if method == "subscribe":
        return "server"  # a consumer handles the topic
    return None


class QueueExtractor(GoBoundaryExtractor):
    """Message-queue producers (Publish/Produce) and consumers (Subscribe)."""

    def mechanism(self) -> str:
        return "queue"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_SELECTOR_CALL, root):
            role = _queue_role(_text(caps["method"][0]).lower())
            if role is None:
                continue
            topic = _first_string(caps["args"][0])
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


GO_DEFAULT_BOUNDARY_EXTRACTORS: list[GoBoundaryExtractor] = [
    HttpServerExtractor(),
    HttpClientExtractor(),
    QueueExtractor(),
    TemporalExtractor(),
    GrpcExtractor(),
]
