"""Base exceptions for graphlens."""


class GraphLensError(Exception):
    """Base exception for all graphlens errors."""


class AdapterNotFoundError(GraphLensError):
    """No adapter found for the requested language."""


class AdapterError(GraphLensError):
    """Error raised during adapter execution."""


class DuplicateNodeError(GraphLensError):
    """A node with this ID already exists in the graph."""


class DiscoveryError(GraphLensError):
    """Error raised during project discovery."""


class BackendError(GraphLensError):
    """Error raised during graph backend operation."""
