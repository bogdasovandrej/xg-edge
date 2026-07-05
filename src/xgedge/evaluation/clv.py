"""Closing line value (CLV): edge held versus the fair closing price."""
from __future__ import annotations

import numpy as np


def clv_per_bet(taken_odds: np.ndarray, fair_close_prob: np.ndarray) -> np.ndarray:
    """CLV per bet: expected return at taken odds under the fair closing prob.

    ``taken_odds * fair_close_prob - 1``: positive means the bet beat the
    closing line.
    """
    return np.asarray(taken_odds, dtype=float) * np.asarray(
        fair_close_prob, dtype=float
    ) - 1.0


def summarize_clv(clvs: np.ndarray, n_boot: int = 10000, seed: int = 0) -> dict:
    """Summarize a CLV sample with a bootstrap 95% CI of the mean.

    Returns ``{"mean", "median", "share_positive", "ci_low", "ci_high", "n"}``.
    Deterministic for a given seed; all-NaN summary when the sample is empty.
    """
    clvs = np.asarray(clvs, dtype=float)
    n = int(clvs.size)
    if n == 0:
        nan = float("nan")
        return {
            "mean": nan,
            "median": nan,
            "share_positive": nan,
            "ci_low": nan,
            "ci_high": nan,
            "n": 0,
        }

    rng = np.random.default_rng(seed)
    boot_means = np.empty(n_boot)
    # chunked resampling keeps peak memory bounded for large bet samples
    chunk = 1000
    for start in range(0, n_boot, chunk):
        size = min(chunk, n_boot - start)
        idx = rng.integers(0, n, size=(size, n))
        boot_means[start : start + size] = clvs[idx].mean(axis=1)
    ci_low, ci_high = np.percentile(boot_means, [2.5, 97.5])

    return {
        "mean": float(clvs.mean()),
        "median": float(np.median(clvs)),
        "share_positive": float((clvs > 0).mean()),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
        "n": n,
    }
