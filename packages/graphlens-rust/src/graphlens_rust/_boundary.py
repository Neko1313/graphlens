"""
Cross-language boundary extractors for Rust (TCK-6).

Server: axum ``.route("/x", get(handler))`` (verb is the handler wrapper)
and actix/rocket ``#[get("/x")]`` attribute macros.  Client: reqwest
``client.get("/x")``.  Keys go through the shared ``normalize_http_path``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from graphlens import BoundaryRef, normalize_http_path

from graphlens_rust._queries import run_query

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_HTTP_VERBS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)
# axum ``route(path, handler)``: the handler wrapper is the 2nd argument.
_HANDLER_ARG_INDEX = 1

# ``recv.method(args)`` — axum ``.route(...)`` / reqwest ``client.get(...)``.
_Q_METHOD_CALL = """
(call_expression
  function: (field_expression
    value: (_) @recv
    field: (field_identifier) @method)
  arguments: (arguments) @args)
"""
# ``#[get("/x")]`` attribute macro.
_Q_ATTRIBUTE = """
(attribute_item
  (attribute (identifier) @attr arguments: (token_tree) @args))
"""


def _text(node: TSNode | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8")


def _pos(node: TSNode) -> tuple[int, int]:
    """Return the 1-based ``(line, col)`` of a node's start."""
    return node.start_point[0] + 1, node.start_point[1] + 1


def _string_content(node: TSNode) -> str | None:
    """Return the content of a Rust string literal, else None."""
    if node.type == "string_literal":
        for child in node.children:
            if child.type == "string_content":
                return _text(child)
        return ""
    return None


def _first_string(args: TSNode) -> str | None:
    """Return the first argument's content if it is a string literal."""
    for child in args.named_children:
        return _string_content(child)
    return None


def _handler_verb(args: TSNode) -> str | None:
    """For axum ``route(path, get(h))``: the verb in the 2nd argument."""
    positional = args.named_children
    if len(positional) <= _HANDLER_ARG_INDEX:
        return None
    handler = positional[_HANDLER_ARG_INDEX]
    if handler.type != "call_expression":
        return None
    fn = handler.child_by_field_name("function")
    verb = _text(fn).lower()
    return verb if verb in _HTTP_VERBS else None


def _string_in_tree(tree: TSNode) -> str | None:
    """Return the first string literal inside an attribute token tree."""
    for child in tree.children:
        if child.type == "string_literal":
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


class RustBoundaryExtractor(ABC):
    """Recognizes one boundary mechanism in a parsed Rust file."""

    @abstractmethod
    def mechanism(self) -> str:
        """Return the boundary family this extractor emits."""
        ...

    @abstractmethod
    def extract(self, root: TSNode) -> list[BoundaryRef]:
        """Return every boundary port found under ``root``."""
        ...


class HttpServerExtractor(RustBoundaryExtractor):
    """axum ``.route(...)`` and actix/rocket ``#[get(...)]`` routes."""

    def mechanism(self) -> str:
        return "http"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_METHOD_CALL, root):
            if _text(caps["method"][0]) != "route":
                continue
            args = caps["args"][0]
            path = _first_string(args)
            verb = _handler_verb(args)
            if path is None or verb is None:
                continue
            refs.append(
                _ref("server", verb.upper(), path, caps["method"][0])
            )
        for caps in run_query(_Q_ATTRIBUTE, root):
            verb = _text(caps["attr"][0]).lower()
            if verb not in _HTTP_VERBS:
                continue
            path = _string_in_tree(caps["args"][0])
            if path is None:
                continue
            refs.append(
                _ref("server", verb.upper(), path, caps["attr"][0])
            )
        return refs


class HttpClientExtractor(RustBoundaryExtractor):
    """reqwest ``client.get("/x")`` / ``client.post("/x")`` calls."""

    def mechanism(self) -> str:
        return "http"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_METHOD_CALL, root):
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


# gRPC (tonic): a client is built from a generated ``<Service>Client`` type
# via ``UserServiceClient::new(channel)`` or ``::connect(addr).await``; its
# methods are snake_case (``get_user``) and map to the PascalCase proto RPC.
_Q_LET_IDENT = """
(let_declaration
  pattern: (identifier) @var
  value: (_) @value)
"""
_GRPC_CTORS = frozenset({"new", "connect"})
_GRPC_CLIENT = "Client"


def _snake_to_pascal(name: str) -> str:
    """``get_user`` -> ``GetUser`` so Rust keys line up with Go/Python."""
    return "".join(part[:1].upper() + part[1:] for part in name.split("_"))


def _grpc_service_from_value(value: TSNode) -> str | None:
    """Return the service from a ``<Service>Client::new`` ctor under value."""
    stack = [value]
    while stack:
        node = stack.pop()
        if node.type == "scoped_identifier" and (
            _text(node.child_by_field_name("name")) in _GRPC_CTORS
        ):
            last = _text(
                node.child_by_field_name("path")
            ).rsplit("::", 1)[-1]
            if last.endswith(_GRPC_CLIENT) and len(last) > len(_GRPC_CLIENT):
                return last[: -len(_GRPC_CLIENT)]
        stack.extend(node.children)
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


class GrpcExtractor(RustBoundaryExtractor):
    """tonic gRPC client calls on a generated ``<Service>Client`` stub."""

    def mechanism(self) -> str:
        return "grpc"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        clients: dict[str, str] = {}
        for caps in run_query(_Q_LET_IDENT, root):
            service = _grpc_service_from_value(caps["value"][0])
            if service is not None:
                clients[_text(caps["var"][0])] = service
        return [
            _grpc_ref(
                service,
                _snake_to_pascal(_text(caps["method"][0])),
                caps["method"][0],
            )
            for caps in run_query(_Q_METHOD_CALL, root)
            if (service := clients.get(_text(caps["recv"][0]))) is not None
        ]


def _queue_role(method: str) -> str | None:
    """Map a queue method name to a boundary role, else None."""
    if method in ("publish", "produce"):
        return "client"  # a producer invokes the topic
    if method == "subscribe":
        return "server"  # a consumer handles the topic
    return None


class QueueExtractor(RustBoundaryExtractor):
    """Message-queue producers (publish/produce) and consumers (subscribe)."""

    def mechanism(self) -> str:
        return "queue"

    def extract(self, root: TSNode) -> list[BoundaryRef]:
        refs: list[BoundaryRef] = []
        for caps in run_query(_Q_METHOD_CALL, root):
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


RUST_DEFAULT_BOUNDARY_EXTRACTORS: list[RustBoundaryExtractor] = [
    HttpServerExtractor(),
    HttpClientExtractor(),
    QueueExtractor(),
    GrpcExtractor(),
]
