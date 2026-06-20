"""Tests for ResolverStatus."""

from graphlens import RESOLVER_STATUS_KEY, ResolverStatus


def test_values() -> None:
    assert ResolverStatus.OK.value == "ok"
    assert ResolverStatus.DEGRADED.value == "degraded"
    assert ResolverStatus.UNAVAILABLE.value == "unavailable"


def test_key_constant() -> None:
    assert RESOLVER_STATUS_KEY == "resolver_status"


def test_combine_returns_worst() -> None:
    assert ResolverStatus.combine([]) is ResolverStatus.OK
    assert (
        ResolverStatus.combine([ResolverStatus.OK, ResolverStatus.OK])
        is ResolverStatus.OK
    )
    assert (
        ResolverStatus.combine([ResolverStatus.OK, ResolverStatus.DEGRADED])
        is ResolverStatus.DEGRADED
    )
    assert (
        ResolverStatus.combine(
            [ResolverStatus.DEGRADED, ResolverStatus.UNAVAILABLE]
        )
        is ResolverStatus.UNAVAILABLE
    )
