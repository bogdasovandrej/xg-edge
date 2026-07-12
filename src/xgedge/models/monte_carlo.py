"""Monte Carlo validation utilities for Dixon-Coles score distributions.

The production market probabilities remain analytical. Simulation is an
independent convergence check and a tool for scenario exploration.
"""
from __future__ import annotations

import numpy as np

from xgedge.models.dixon_coles import score_matrix


def simulate_scorelines(
    lh: float,
    la: float,
    rho: float = 0.0,
    n_simulations: int = 100_000,
    max_goals: int = 10,
    seed: int | None = None,
) -> np.ndarray:
    """Draw scorelines from the same truncated Dixon-Coles matrix as production."""
    if (
        isinstance(n_simulations, bool)
        or not isinstance(n_simulations, (int, np.integer))
        or n_simulations <= 0
    ):
        raise ValueError("n_simulations must be a positive integer")

    matrix = score_matrix(lh, la, rho=rho, max_goals=max_goals)
    rng = np.random.default_rng(seed)
    flat = rng.choice(matrix.size, size=int(n_simulations), p=matrix.ravel())
    n_cols = matrix.shape[1]
    return np.column_stack((flat // n_cols, flat % n_cols))


def estimate_market_probabilities(
    scores: np.ndarray,
    total_line: float = 2.5,
) -> dict[str, float | int]:
    """Estimate 1X2, total and BTTS probabilities plus Bernoulli standard errors."""
    values = np.asarray(scores)
    if values.ndim != 2 or values.shape[1] != 2 or len(values) == 0:
        raise ValueError("scores must be a non-empty array with shape (n, 2)")
    if not np.issubdtype(values.dtype, np.number):
        raise ValueError("scores must be numeric")
    if not np.isfinite(values).all():
        raise ValueError("scores must contain only finite values")
    if np.any(values < 0) or np.any(values != np.floor(values)):
        raise ValueError("scores must contain non-negative integer goal counts")
    if not np.isfinite(total_line):
        raise ValueError("total_line must be finite")

    home = values[:, 0]
    away = values[:, 1]
    indicators = {
        "p_home": home > away,
        "p_draw": home == away,
        "p_away": home < away,
        "p_over": home + away > total_line,
        "p_btts": (home > 0) & (away > 0),
    }

    n = len(values)
    result: dict[str, float | int] = {"n_simulations": n}
    for name, indicator in indicators.items():
        probability = float(indicator.mean())
        result[name] = probability
        result[f"se_{name.removeprefix('p_')}"] = float(
            np.sqrt(probability * (1.0 - probability) / n)
        )
    return result


def monte_carlo_markets(
    lh: float,
    la: float,
    rho: float = 0.0,
    n_simulations: int = 100_000,
    max_goals: int = 10,
    total_line: float = 2.5,
    seed: int | None = None,
) -> dict[str, float | int]:
    """Simulate scorelines and return market estimates with sampling uncertainty."""
    scores = simulate_scorelines(
        lh,
        la,
        rho=rho,
        n_simulations=n_simulations,
        max_goals=max_goals,
        seed=seed,
    )
    return estimate_market_probabilities(scores, total_line=total_line)
