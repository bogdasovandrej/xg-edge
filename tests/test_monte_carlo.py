"""Tests for the optional Monte Carlo validation layer."""
from __future__ import annotations

import numpy as np
import pytest

from xgedge.markets.markets import prob_btts, prob_over, probs_1x2
from xgedge.models.dixon_coles import score_matrix
from xgedge.models.monte_carlo import (
    estimate_market_probabilities,
    monte_carlo_markets,
    simulate_scorelines,
)


def test_simulation_is_reproducible_for_a_fixed_seed() -> None:
    first = simulate_scorelines(1.5, 1.1, rho=-0.07, n_simulations=500, seed=9)
    second = simulate_scorelines(1.5, 1.1, rho=-0.07, n_simulations=500, seed=9)
    np.testing.assert_array_equal(first, second)


def test_monte_carlo_converges_to_analytical_markets() -> None:
    lh, la, rho, n = 1.55, 1.05, -0.08, 250_000
    matrix = score_matrix(lh, la, rho=rho)
    p_home, p_draw, p_away = probs_1x2(matrix)
    exact = {
        "p_home": p_home,
        "p_draw": p_draw,
        "p_away": p_away,
        "p_over": prob_over(matrix, 2.5),
        "p_btts": prob_btts(matrix),
    }

    estimated = monte_carlo_markets(
        lh, la, rho=rho, n_simulations=n, total_line=2.5, seed=17
    )

    assert estimated["n_simulations"] == n
    assert (
        estimated["p_home"] + estimated["p_draw"] + estimated["p_away"]
        == pytest.approx(1.0)
    )
    for name, expected in exact.items():
        suffix = name.removeprefix("p_")
        tolerance = 5.0 * estimated[f"se_{suffix}"] + 5e-4
        assert abs(estimated[name] - expected) <= tolerance


@pytest.mark.parametrize("n_simulations", [0, -1, 2.5, True])
def test_simulation_rejects_invalid_sample_counts(n_simulations) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        simulate_scorelines(1.4, 1.0, n_simulations=n_simulations)


@pytest.mark.parametrize(
    "scores",
    [
        np.array([1, 0]),
        np.array([[1.5, 0.0]]),
        np.array([[1.0, np.nan]]),
        np.empty((0, 2)),
    ],
)
def test_market_estimator_rejects_invalid_scores(scores: np.ndarray) -> None:
    with pytest.raises(ValueError):
        estimate_market_probabilities(scores)
