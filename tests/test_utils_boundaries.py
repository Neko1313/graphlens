"""Tests for shared HTTP boundary-key normalization."""

from graphlens import normalize_http_path


def test_strips_scheme_and_host() -> None:
    assert normalize_http_path("http://h/api/x") == "/api/x"


def test_scheme_without_path() -> None:
    assert normalize_http_path("http://host") == "/"


def test_adds_leading_slash() -> None:
    assert normalize_http_path("api/x") == "/api/x"


def test_brace_param() -> None:
    assert normalize_http_path("/u/{id}") == "/u/{}"


def test_flask_converter() -> None:
    assert normalize_http_path("/u/<int:id>") == "/u/{}"


def test_colon_param() -> None:
    assert normalize_http_path("/u/:id") == "/u/{}"


def test_numeric_segment() -> None:
    assert normalize_http_path("/users/42/posts") == "/users/{}/posts"


def test_strips_query_and_fragment() -> None:
    assert normalize_http_path("/x?a=1#z") == "/x"


def test_trailing_slash_dropped() -> None:
    assert normalize_http_path("/users/") == "/users"


def test_root_kept() -> None:
    assert normalize_http_path("/") == "/"


def test_all_slashes_collapse_to_root() -> None:
    assert normalize_http_path("//") == "/"


def test_cross_language_keys_match() -> None:
    """A FastAPI route and an Express client path reduce to one key."""
    assert normalize_http_path("/users/{id}") == normalize_http_path(
        "/users/:id"
    )
    assert normalize_http_path("/users/{id}") == normalize_http_path(
        "/users/1"
    )
