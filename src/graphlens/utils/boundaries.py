"""
Shared boundary-key normalization (used by every language adapter).

Cross-language matching only works if a Python ``@app.get("/users/{id}")``
and a TypeScript ``fetch("/users/1")`` reduce to the *same* key.  Keeping
this normalization in core guarantees all adapters agree byte-for-byte.
"""

from __future__ import annotations

import re

_BRACE_PARAM = re.compile(r"\{[^}]*\}")
_ANGLE_PARAM = re.compile(r"<[^>]*>")
_COLON_PARAM = re.compile(r":[^/]+")


def normalize_http_path(raw: str) -> str:
    """
    Normalize a route/URL into a host- and param-agnostic path key.

    * strips scheme + host (``http://h/api/x`` -> ``/api/x``)
    * strips query and fragment
    * collapses path params to ``{}`` across framework styles:
      ``{id}`` (FastAPI/Starlette), ``<int:id>`` (Flask), ``:id`` (Express)
    * collapses concrete numeric ids so ``/users/1`` meets ``/users/{}``
    * drops a trailing slash (except the root ``/``)
    """
    path = raw.strip()
    if "://" in path:
        after = path.split("://", 1)[1]
        slash = after.find("/")
        path = after[slash:] if slash != -1 else "/"
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    path = _BRACE_PARAM.sub("{}", path)
    path = _ANGLE_PARAM.sub("{}", path)
    path = _COLON_PARAM.sub("{}", path)
    path = "/".join(
        "{}" if seg.isdigit() else seg for seg in path.split("/")
    )
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return path
