"""Probability and settlement coverage for automated score markets."""
from __future__ import annotations

import pytest

from xgedge.markets.paper_markets import (
    market_settlement_distribution,
    market_probability,
    score_matrix,
    settle_score_market,
    supported_line,
)


def test_score_probabilities_cover_primary_goal_markets() -> None:
    matrix = score_matrix(1.7, 1.1)
    home = market_probability(matrix, market="1x2", selection="home")
    draw = market_probability(matrix, market="1x2", selection="draw")
    away = market_probability(matrix, market="1x2", selection="away")
    assert home + draw + away == pytest.approx(1.0)
    assert 0 < market_probability(
        matrix, market="totals", selection="over", line=2.5
    ) < 1
    assert 0 < market_probability(matrix, market="btts", selection="yes") < 1
    assert 0 < market_probability(
        matrix, market="asian_handicap", selection="home", line=-0.5
    ) < 1
    assert 0 < market_probability(
        matrix, market="draw_no_bet", selection="home"
    ) < 1


@pytest.mark.parametrize(
    ("market", "selection", "line", "score", "expected"),
    [
        ("1x2", "home", None, (2, 1), "win"),
        ("btts", "yes", None, (2, 1), "win"),
        ("btts", "no", None, (2, 1), "loss"),
        ("totals", "over", 2.5, (2, 1), "win"),
        ("totals", "under", 3.0, (2, 1), "push"),
        ("team_totals", "home_over", 1.5, (2, 1), "win"),
        ("double_chance", "draw_away", None, (1, 1), "win"),
        ("draw_no_bet", "home", None, (1, 1), "push"),
        ("asian_handicap", "home", -1.0, (2, 1), "push"),
        ("asian_handicap", "away", 1.5, (2, 1), "win"),
    ],
)
def test_settle_score_markets(
    market: str,
    selection: str,
    line: float | None,
    score: tuple[int, int],
    expected: str,
) -> None:
    assert settle_score_market(
        market=market,
        selection=selection,
        line=line,
        home_goals=score[0],
        away_goals=score[1],
    ) == expected


@pytest.mark.parametrize(
    ("market", "selection", "line", "score", "expected"),
    [
        ("asian_handicap", "home", 1.25, (1, 2), "half_win"),
        ("asian_handicap", "home", .75, (1, 2), "half_loss"),
        ("asian_handicap", "home", -1.25, (2, 0), "win"),
        ("asian_handicap", "home", -1.25, (1, 0), "half_loss"),
        ("totals", "over", 2.75, (2, 1), "half_win"),
        ("totals", "over", 2.75, (1, 1), "loss"),
        ("totals", "under", 3.25, (2, 1), "half_win"),
        ("totals", "under", 3.25, (2, 2), "loss"),
    ],
)
def test_quarter_lines_settle_exact_half_stakes(
    market: str,
    selection: str,
    line: float,
    score: tuple[int, int],
    expected: str,
) -> None:
    assert supported_line(line) == line
    assert settle_score_market(
        market=market,
        selection=selection,
        line=line,
        home_goals=score[0],
        away_goals=score[1],
    ) == expected


def test_push_aware_distribution_solves_fair_and_trigger_prices() -> None:
    distribution = market_settlement_distribution(
        score_matrix(1.7, 1.1), market="totals", selection="under", line=3.0
    )
    fair = distribution.fair_odds()
    trigger = distribution.trigger_odds(.03)
    assert distribution.expected_value(fair) == pytest.approx(0.0, abs=1e-9)
    assert distribution.expected_value(trigger) == pytest.approx(.03, abs=1e-9)
    assert trigger > fair > 1.0
