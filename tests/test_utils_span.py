"""Tests for Span utility."""

import dataclasses

import pytest

from code_graph.utils.span import Span


class TestSpan:
    def test_creation(self) -> None:
        s = Span(start_line=1, start_col=1, end_line=5, end_col=20)
        assert s.start_line == 1
        assert s.start_col == 1
        assert s.end_line == 5
        assert s.end_col == 20

    def test_frozen(self) -> None:
        s = Span(1, 1, 5, 20)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            s.start_line = 99  # type: ignore

    def test_equality(self) -> None:
        s1 = Span(1, 1, 5, 20)
        s2 = Span(1, 1, 5, 20)
        assert s1 == s2

    def test_inequality(self) -> None:
        s1 = Span(1, 1, 5, 20)
        s2 = Span(2, 1, 5, 20)
        assert s1 != s2

    def test_single_line(self) -> None:
        s = Span(3, 4, 3, 30)
        assert s.start_line == s.end_line

    def test_all_ones(self) -> None:
        s = Span(1, 1, 1, 1)
        assert s.start_line == 1
