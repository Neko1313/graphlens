"""Tests for shared HTTP boundary-key normalization."""

from graphlens import normalize_http_path


def test_strips_scheme_and_host() -> None:
    assert normalize_http_path("http://h/api/x") == "/api/x"


def test_scheme_without_path() -> None:
    assert normalize_http_path("http://host") == "/"


def test_colon_param_at_segment_start() -> None:
    assert normalize_http_path("/users/:id") == "/users/{}"
    assert (
        normalize_http_path("/users/:id/posts/:pid") == "/users/{}/posts/{}"
    )


def test_literal_colon_in_segment_preserved() -> None:
    # A colon inside a segment (custom verbs, digests) is not a path param.
    assert (
        normalize_http_path("/v1/users/123:activate")
        == "/v1/users/123:activate"
    )
    assert normalize_http_path("/repo/sha256:abc") == "/repo/sha256:abc"


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
