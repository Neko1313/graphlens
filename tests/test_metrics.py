from graphlens import RESOLVER_METRICS_KEY, ResolverMetrics


def test_metrics_key_is_stable():
    assert RESOLVER_METRICS_KEY == "resolver_metrics"


def test_resolved_pct_zero_when_no_queries():
    assert ResolverMetrics().resolved_pct == 0.0


def test_resolved_pct_computes_share():
    m = ResolverMetrics(queries=4, resolved=3)
    assert m.resolved_pct == 75.0


def test_merge_folds_counters():
    a = ResolverMetrics(
        queries=2, resolved=2, internal=1, external=1, seconds=0.5
    )
    b = ResolverMetrics(
        queries=3, resolved=1, internal=1, unresolved=2, seconds=0.25
    )
    a.merge(b)
    assert a.queries == 5
    assert a.resolved == 3
    assert a.internal == 2
    assert a.external == 1
    assert a.unresolved == 2
    assert a.seconds == 0.75


def test_as_dict_is_json_friendly_and_rounds():
    m = ResolverMetrics(
        queries=10, resolved=9, internal=7, external=2,
        unresolved=1, seconds=1.23456,
    )
    d = m.as_dict()
    assert d == {
        "queries": 10,
        "resolved": 9,
        "internal": 7,
        "external": 2,
        "unresolved": 1,
        "seconds": 1.235,
        "resolved_pct": 90.0,
    }
