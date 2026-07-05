"""Tests for xgedge.markets.markets (self-contained fixtures)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from xgedge.markets.markets import (
    ah_home_probs,
    ev_ah_home,
    prob_btts,
    prob_over,
    probs_1x2,
    top_scores,
)

MAX_GOALS = 10


def poisson_matrix(lh: float, la: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    """Independent-Poisson joint score matrix, normalized to sum to 1."""

    def pmf(lam: float) -> np.ndarray:
        return np.array(
            [math.exp(-lam) * lam**k / math.factorial(k) for k in range(max_goals + 1)]
        )

    M = np.outer(pmf(lh), pmf(la))
    return M / M.sum()


def test_probs_1x2_sums_to_one() -> None:
    M = poisson_matrix(1.6, 1.1)
    p_home, p_draw, p_away = probs_1x2(M)
    assert p_home + p_draw + p_away == pytest.approx(1.0, abs=1e-12)
    assert p_home > p_away  # higher home lambda must favour home


def test_probs_1x2_symmetric_matrix() -> None:
    M = poisson_matrix(1.3, 1.3)
    p_home, _, p_away = probs_1x2(M)
    assert p_home == pytest.approx(p_away, abs=1e-12)


def test_prob_over_25_complements_total_le_2() -> None:
    M = poisson_matrix(1.4, 1.2)
    totals = np.add.outer(np.arange(MAX_GOALS + 1), np.arange(MAX_GOALS + 1))
    p_le_2 = M[totals <= 2].sum()
    assert prob_over(M, 2.5) + p_le_2 == pytest.approx(1.0, abs=1e-12)


def test_prob_over_asserts_on_integer_line() -> None:
    M = poisson_matrix(1.4, 1.2)
    with pytest.raises(AssertionError):
        prob_over(M, 2.0)


def test_btts_all_mass_at_0_0_is_zero() -> None:
    M = np.zeros((MAX_GOALS + 1, MAX_GOALS + 1))
    M[0, 0] = 1.0
    assert prob_btts(M) == 0.0


def test_btts_all_mass_at_1_1_is_one() -> None:
    M = np.zeros((MAX_GOALS + 1, MAX_GOALS + 1))
    M[1, 1] = 1.0
    assert prob_btts(M) == 1.0


def test_ah_zero_line_partition_and_push_is_draw() -> None:
    M = poisson_matrix(1.5, 1.0)
    p_home, p_draw, _ = probs_1x2(M)
    r = ah_home_probs(M, 0.0)
    assert r["win"] + r["push"] + r["lose"] == pytest.approx(1.0, abs=1e-12)
    assert r["push"] == pytest.approx(p_draw, abs=1e-12)
    assert r["win"] == pytest.approx(p_home, abs=1e-12)


def test_ah_minus_half_win_equals_home_win_prob() -> None:
    M = poisson_matrix(1.5, 1.0)
    p_home, _, _ = probs_1x2(M)
    r = ah_home_probs(M, -0.5)
    assert r["push"] == 0.0
    assert r["win"] == pytest.approx(p_home, abs=1e-12)


def test_ah_rejects_quarter_line() -> None:
    M = poisson_matrix(1.5, 1.0)
    with pytest.raises(AssertionError):
        ah_home_probs(M, -0.25)


def test_ev_quarter_line_is_average_of_adjacent_lines() -> None:
    M = poisson_matrix(1.5, 1.0)
    odds = 1.9
    ev_quarter = ev_ah_home(M, -0.25, odds)
    ev_zero = ev_ah_home(M, 0.0, odds)
    ev_half = ev_ah_home(M, -0.5, odds)
    assert ev_quarter == pytest.approx(0.5 * (ev_zero + ev_half), abs=1e-12)


def test_ev_flat_line_matches_probabilities() -> None:
    M = poisson_matrix(1.5, 1.0)
    odds = 2.05
    r = ah_home_probs(M, -1.0)
    expected = r["win"] * (odds - 1.0) - r["lose"]
    assert ev_ah_home(M, -1.0, odds) == pytest.approx(expected, abs=1e-12)


def test_top_scores_sorted_and_correct() -> None:
    M = poisson_matrix(1.1, 0.9)
    top = top_scores(M, k=5)
    assert len(top) == 5
    probs = [p for _, p in top]
    assert probs == sorted(probs, reverse=True)
    i, j = np.unravel_index(np.argmax(M), M.shape)
    assert top[0][0] == (int(i), int(j))
    assert top[0][1] == pytest.approx(float(M[i, j]))
