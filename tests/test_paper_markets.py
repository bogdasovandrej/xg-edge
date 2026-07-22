"""Probability and settlement coverage for automated score markets."""
from __future__ import annotations

import pytest

from xgedge.markets.paper_markets import (
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


def test_quarter_lines_fail_closed_until_half_settlement_is_supported() -> None:
    assert supported_line(2.25) is None
    with pytest.raises(ValueError, match="whole or half"):
        settle_score_market(
            market="totals",
            selection="over",
            line=2.25,
            home_goals=2,
            away_goals=1,
        )
