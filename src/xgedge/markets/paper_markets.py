"""Probability and settlement rules for regulation-time PAPER markets.

The module is deliberately limited to markets that can be resolved from the
official 90-minute score.  Corners, cards and qualification require additional
official result fields and therefore fail closed elsewhere.
"""
from __future__ import annotations

from math import exp, factorial, isfinite
from typing import Final

import numpy as np


SUPPORTED_SCORE_MARKETS: Final[frozenset[str]] = frozenset({
    "1x2",
    "totals",
    "team_totals",
    "btts",
    "asian_handicap",
    "double_chance",
    "draw_no_bet",
})


def canonical_market(value: object) -> str:
    aliases = {
        "h2h": "1x2",
        "match_result": "1x2",
        "totals_2_5": "totals",
        "spreads": "asian_handicap",
        "spread": "asian_handicap",
        "dnb": "draw_no_bet",
    }
    parsed = str(value or "1x2").strip().casefold()
    return aliases.get(parsed, parsed)


def supported_line(value: object) -> float | None:
    """Return a whole/half line; quarter lines need half-win accounting."""
    if value is None or isinstance(value, bool):
        return None
    try:
        line = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(line) or abs(line * 2.0 - round(line * 2.0)) > 1e-9:
        return None
    return line


def _poisson(lambda_: float, maximum: int) -> np.ndarray:
    if not isfinite(lambda_) or lambda_ <= 0:
        raise ValueError("expected goals must be finite and positive")
    return np.asarray(
        [exp(-lambda_) * lambda_**goals / factorial(goals) for goals in range(maximum + 1)],
        dtype=float,
    )


def score_matrix(home_xg: float, away_xg: float, *, maximum: int = 14) -> np.ndarray:
    """Return a normalized independent-Poisson score matrix for market pricing."""
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 8:
        raise ValueError("maximum goals must be an integer >= 8")
    matrix = np.outer(_poisson(float(home_xg), maximum), _poisson(float(away_xg), maximum))
    total = float(matrix.sum())
    if not isfinite(total) or total <= 0:
        raise ValueError("score matrix has invalid mass")
    return matrix / total


def _conditional(win: float, push: float = 0.0) -> float:
    active = 1.0 - float(push)
    if active <= 1e-12:
        raise ValueError("market has no active probability mass")
    value = float(win) / active
    if not 0.0 < value < 1.0:
        raise ValueError("conditional market probability must be in (0, 1)")
    return value


def market_probability(
    matrix: np.ndarray,
    *,
    market: str,
    selection: str,
    line: float | None = None,
) -> float:
    """Return win probability conditional on no push for one score market."""
    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2 or values.shape[0] != values.shape[1]:
        raise ValueError("score matrix must be square")
    if not np.isfinite(values).all() or abs(float(values.sum()) - 1.0) > 1e-6:
        raise ValueError("score matrix must contain normalized finite probabilities")
    kind = canonical_market(market)
    side = str(selection or "").strip().casefold()
    goals = np.arange(values.shape[0])
    home = goals[:, None]
    away = goals[None, :]

    if kind == "1x2":
        masks = {"home": home > away, "draw": home == away, "away": home < away}
        if side not in masks:
            raise ValueError("unsupported 1x2 selection")
        return float(values[masks[side]].sum())

    if kind == "btts":
        yes = (home > 0) & (away > 0)
        if side not in {"yes", "no"}:
            raise ValueError("unsupported BTTS selection")
        return float(values[yes if side == "yes" else ~yes].sum())

    if kind == "double_chance":
        masks = {
            "home_draw": home >= away,
            "home_away": home != away,
            "draw_away": home <= away,
        }
        if side not in masks:
            raise ValueError("unsupported double-chance selection")
        return float(values[masks[side]].sum())

    if kind == "draw_no_bet":
        if side not in {"home", "away"}:
            raise ValueError("unsupported draw-no-bet selection")
        win = home > away if side == "home" else away > home
        push = home == away
        return _conditional(float(values[win].sum()), float(values[push].sum()))

    parsed_line = supported_line(line)
    if parsed_line is None:
        raise ValueError("market requires a whole or half line")

    if kind == "totals":
        if side not in {"over", "under"}:
            raise ValueError("unsupported totals selection")
        metric = home + away
    elif kind == "team_totals":
        if side not in {"home_over", "home_under", "away_over", "away_under"}:
            raise ValueError("unsupported team-total selection")
        metric = np.broadcast_to(
            home if side.startswith("home_") else away,
            values.shape,
        )
        side = side.split("_", 1)[1]
    elif kind == "asian_handicap":
        if side not in {"home", "away"}:
            raise ValueError("unsupported handicap selection")
        metric = (home - away if side == "home" else away - home) + parsed_line
        win = metric > 0
        push = metric == 0
        return _conditional(float(values[win].sum()), float(values[push].sum()))
    else:
        raise ValueError(f"unsupported score market: {kind}")

    win = metric > parsed_line if side == "over" else metric < parsed_line
    push = metric == parsed_line
    return _conditional(float(values[win].sum()), float(values[push].sum()))


def settle_score_market(
    *,
    market: str,
    selection: str,
    line: float | None,
    home_goals: int,
    away_goals: int,
) -> str:
    """Resolve a supported selection to win/loss/push from the 90-minute score."""
    if (
        isinstance(home_goals, bool)
        or isinstance(away_goals, bool)
        or not isinstance(home_goals, int)
        or not isinstance(away_goals, int)
        or home_goals < 0
        or away_goals < 0
    ):
        raise ValueError("90-minute goals must be non-negative integers")
    kind = canonical_market(market)
    side = str(selection or "").strip().casefold()

    if kind == "1x2":
        actual = "home" if home_goals > away_goals else "away" if away_goals > home_goals else "draw"
        if side not in {"home", "draw", "away"}:
            raise ValueError("unsupported 1x2 selection")
        return "win" if side == actual else "loss"

    if kind == "btts":
        if side not in {"yes", "no"}:
            raise ValueError("unsupported BTTS selection")
        actual = home_goals > 0 and away_goals > 0
        return "win" if actual == (side == "yes") else "loss"

    if kind == "double_chance":
        wins = {
            "home_draw": home_goals >= away_goals,
            "home_away": home_goals != away_goals,
            "draw_away": home_goals <= away_goals,
        }
        if side not in wins:
            raise ValueError("unsupported double-chance selection")
        return "win" if wins[side] else "loss"

    if kind == "draw_no_bet":
        if side not in {"home", "away"}:
            raise ValueError("unsupported draw-no-bet selection")
        if home_goals == away_goals:
            return "push"
        actual = "home" if home_goals > away_goals else "away"
        return "win" if side == actual else "loss"

    parsed_line = supported_line(line)
    if parsed_line is None:
        raise ValueError("market requires a whole or half line")
    if kind == "totals":
        if side not in {"over", "under"}:
            raise ValueError("unsupported totals selection")
        metric = float(home_goals + away_goals)
    elif kind == "team_totals":
        if side not in {"home_over", "home_under", "away_over", "away_under"}:
            raise ValueError("unsupported team-total selection")
        metric = float(home_goals if side.startswith("home_") else away_goals)
        side = side.split("_", 1)[1]
    elif kind == "asian_handicap":
        if side not in {"home", "away"}:
            raise ValueError("unsupported handicap selection")
        metric = float(home_goals - away_goals if side == "home" else away_goals - home_goals)
        adjusted = metric + parsed_line
        return "win" if adjusted > 0 else "loss" if adjusted < 0 else "push"
    else:
        raise ValueError(f"unsupported score market: {kind}")

    if metric == parsed_line:
        return "push"
    return "win" if (metric > parsed_line) == (side == "over") else "loss"
