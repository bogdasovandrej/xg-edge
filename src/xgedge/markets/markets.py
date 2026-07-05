"""Betting-market probabilities and expected values from a score matrix.

Every function takes ``M``, a joint score-probability matrix (rows = home
goals, columns = away goals, entries summing to 1) as produced by
``xgedge.models.dixon_coles.score_matrix``.

Asian handicap convention: the handicap is added to HOME goals; the home
side wins if ``home + h > away``, pushes if ``home + h == away`` (only
possible on integer lines), loses otherwise.
"""
from __future__ import annotations

import numpy as np


def probs_1x2(M: np.ndarray) -> tuple[float, float, float]:
    """Return (p_home, p_draw, p_away) from the joint score matrix."""
    M = np.asarray(M, dtype=float)
    p_home = float(np.tril(M, -1).sum())
    p_draw = float(np.trace(M))
    p_away = float(np.triu(M, 1).sum())
    return p_home, p_draw, p_away


def prob_over(M: np.ndarray, line: float) -> float:
    """P(total goals > line). Half-goal lines only, so no push is possible."""
    assert line % 1 == 0.5, f"prob_over supports half-goal lines only, got {line}"
    M = np.asarray(M, dtype=float)
    totals = np.add.outer(np.arange(M.shape[0]), np.arange(M.shape[1]))
    return float(M[totals > line].sum())


def prob_btts(M: np.ndarray) -> float:
    """P(both teams score), i.e. home >= 1 and away >= 1."""
    M = np.asarray(M, dtype=float)
    return float(M[1:, 1:].sum())


def ah_home_probs(M: np.ndarray, handicap: float) -> dict:
    """Win/push/lose probabilities for the home side on an Asian handicap.

    Integer and half lines only; quarter lines are handled by
    :func:`ev_ah_home` via stake splitting.
    """
    assert (handicap * 2) % 1 == 0, (
        f"ah_home_probs supports integer and half lines only, got {handicap}"
    )
    M = np.asarray(M, dtype=float)
    # diff grid is exact: goal counts plus a multiple of 0.5 have no
    # floating-point error, so == 0 is a safe push test.
    diff = np.add.outer(np.arange(M.shape[0]) + handicap, -np.arange(M.shape[1]))
    return {
        "win": float(M[diff > 0].sum()),
        "push": float(M[diff == 0].sum()),
        "lose": float(M[diff < 0].sum()),
    }


def ev_ah_home(M: np.ndarray, handicap: float, odds: float) -> float:
    """Expected value per unit stake of backing home at ``odds`` on a handicap.

    Quarter lines (x.25 / x.75) split the stake in half across the two
    adjacent lines (e.g. -0.25 -> half on 0, half on -0.5). A push returns
    the stake, contributing 0 to EV.
    """
    assert (handicap * 4) % 1 == 0, (
        f"ev_ah_home supports quarter-line multiples of 0.25 only, got {handicap}"
    )
    if (handicap * 2) % 1 == 0:
        p = ah_home_probs(M, handicap)
        return p["win"] * (odds - 1.0) - p["lose"]
    # +/-0.25 offsets are exact in binary floating point.
    return 0.5 * (
        ev_ah_home(M, handicap - 0.25, odds) + ev_ah_home(M, handicap + 0.25, odds)
    )


def top_scores(M: np.ndarray, k: int = 5) -> list[tuple[tuple[int, int], float]]:
    """Return the ``k`` most likely scorelines as ((home, away), prob), descending.

    Ties are broken deterministically by (home goals, away goals) order.
    """
    M = np.asarray(M, dtype=float)
    flat = M.ravel()
    order = np.argsort(-flat, kind="stable")[:k]
    n_cols = M.shape[1]
    return [
        ((int(i // n_cols), int(i % n_cols)), float(flat[i])) for i in order
    ]
