"""Leave-one-out bookmaker consensus: the free, no-LLM edge source."""
from __future__ import annotations

import pytest

from xgedge.decision.market_consensus import (
    ConsensusConfig,
    best_outliers,
    consensus_probability,
    devig_book,
    evaluate_market,
)


def _fair_book(margin: float = 0.05) -> dict[str, float]:
    """A book quoting a 40/30/30 market with the given margin."""
    true = {"home": 0.40, "draw": 0.30, "away": 0.30}
    return {k: 1.0 / (v * (1.0 + margin)) for k, v in true.items()}


def test_devig_removes_the_margin() -> None:
    probabilities = devig_book(_fair_book(0.06))
    assert sum(probabilities.values()) == pytest.approx(1.0, abs=1e-6)
    assert probabilities["home"] == pytest.approx(0.40, abs=0.02)


def test_devig_refuses_an_incomplete_market() -> None:
    """Normalising a partial market would overstate every probability."""
    with pytest.raises(ValueError):
        devig_book({"home": 2.5})


def _book_at(home_probability: float, margin: float = 0.05) -> dict[str, float]:
    """A book pricing `home` at the given true probability, rest split evenly."""
    rest = (1.0 - home_probability) / 2.0
    true = {"home": home_probability, "draw": rest, "away": rest}
    return {k: 1.0 / (v * (1.0 + margin)) for k, v in true.items()}


def test_a_book_is_never_part_of_its_own_benchmark() -> None:
    books = {
        "a": _book_at(0.38), "b": _book_at(0.40), "c": _book_at(0.42),
        "outlier": _book_at(0.30),
    }
    with_outlier = consensus_probability(books, "home")
    without_outlier = consensus_probability(books, "home", exclude="outlier")
    # Excluding the book under test must move the benchmark it is judged
    # against; otherwise its own price quietly votes for itself and drags the
    # consensus toward the very number under test.
    assert with_outlier < without_outlier
    assert without_outlier == pytest.approx(0.40, abs=0.02)


def test_self_reference_would_understate_the_edge() -> None:
    """Including the evaluated book shrinks its measured value toward zero."""
    books = {
        "a": _book_at(0.38), "b": _book_at(0.40), "c": _book_at(0.42),
        "generous": _book_at(0.30),
    }
    honest = consensus_probability(books, "home", exclude="generous")
    self_referential = consensus_probability(books, "home")
    offered = books["generous"]["home"]
    honest_value = (honest * offered - 1.0) * 100.0
    inflated_benchmark_value = (self_referential * offered - 1.0) * 100.0
    assert honest_value > inflated_benchmark_value


def test_outlier_book_is_flagged_and_conforming_books_are_not() -> None:
    books = {
        "a": _fair_book(), "b": _fair_book(), "c": _fair_book(),
        # Offers 3.20 on an outcome the rest of the market prices near 2.50.
        "generous": {**_fair_book(), "home": 3.20},
    }
    result = evaluate_market(books)
    assert result["status"] == "OK"
    flagged = [row for row in result["candidates"] if row["status"] == "OUTLIER_CANDIDATE"]
    assert [row["bookmaker"] for row in flagged] == ["generous"]
    assert flagged[0]["outcome"] == "home"
    assert flagged[0]["value_pct"] > 8.0


def test_a_market_where_every_book_agrees_yields_no_edge() -> None:
    """The circularity check: agreeing books must not manufacture value."""
    books = {name: _fair_book() for name in ("a", "b", "c", "d")}
    result = evaluate_market(books)
    assert all(row["status"] == "BELOW_MIN" for row in result["candidates"])
    # Against a consensus built from identical books, the margin means every
    # offered price sits below fair, so value is negative, never positive.
    assert all(row["value_pct"] < 0 for row in result["candidates"])


def test_two_books_are_not_a_consensus() -> None:
    result = evaluate_market({"a": _fair_book(), "b": _fair_book()})
    assert result["status"] == "INSUFFICIENT_BOOKS"
    assert result["candidates"] == []


def test_config_rejects_a_meaningless_minimum() -> None:
    with pytest.raises(ValueError):
        ConsensusConfig(minimum_books=2).validate()


def test_best_outliers_ranks_across_markets_by_value() -> None:
    small = evaluate_market({
        "a": _fair_book(), "b": _fair_book(), "c": _fair_book(),
        "x": {**_fair_book(), "home": 2.90},
    })
    large = evaluate_market({
        "a": _fair_book(), "b": _fair_book(), "c": _fair_book(),
        "y": {**_fair_book(), "home": 3.60},
    })
    rows = best_outliers([small, large])
    assert [row["bookmaker"] for row in rows][:2] == ["y", "x"]
    assert rows[0]["value_pct"] > rows[1]["value_pct"]


def test_insufficient_books_is_not_reported_as_a_confident_zero() -> None:
    result = evaluate_market({"a": _fair_book()})
    assert result["status"] == "INSUFFICIENT_BOOKS"
    assert "reason" in result
