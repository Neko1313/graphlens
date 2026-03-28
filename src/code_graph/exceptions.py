"""Base exceptions for code-graph."""


class CodeGraphError(Exception):
    """Base exception for all code-graph errors."""


class AdapterNotFoundError(CodeGraphError):
    """No adapter found for the requested language."""


class AdapterError(CodeGraphError):
    """Error raised during adapter execution."""


class DuplicateNodeError(CodeGraphError):
    """A node with this ID already exists in the graph."""


class DiscoveryError(CodeGraphError):
    """Error raised during project discovery."""


class BackendError(CodeGraphError):
    """Error raised during graph backend operation."""
