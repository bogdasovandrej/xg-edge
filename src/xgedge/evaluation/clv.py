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


def summarize_clv(
    clvs: np.ndarray,
    n_boot: int = 10000,
    seed: int = 0,
    groups: np.ndarray | None = None,
) -> dict:
    """Summarize CLV with a deterministic bootstrap 95% CI of the mean.

    When groups are supplied, whole clusters are resampled so correlated
    selections from one match are not treated as independent observations.
    """
    clvs = np.asarray(clvs, dtype=float)
    if clvs.ndim != 1:
        raise ValueError("clvs must be one-dimensional")
    n = int(clvs.size)
    if (
        isinstance(n_boot, bool)
        or not isinstance(n_boot, (int, np.integer))
        or n_boot <= 0
    ):
        raise ValueError("n_boot must be a positive integer")
    if not np.isfinite(clvs).all():
        raise ValueError("clvs must contain only finite values")

    group_arr = None
    if groups is not None:
        group_arr = np.asarray(groups)
        if group_arr.ndim != 1 or group_arr.size != n:
            raise ValueError("groups must be one-dimensional and match clvs")

    if n == 0:
        nan = float("nan")
        return {
            "mean": nan,
            "median": nan,
            "share_positive": nan,
            "ci_low": nan,
            "ci_high": nan,
            "n": 0,
            "n_clusters": 0,
            "bootstrap_unit": "cluster" if group_arr is not None else "bet",
        }

    rng = np.random.default_rng(seed)
    boot_means = np.empty(n_boot)
    chunk = 1000
    if group_arr is None:
        n_clusters = n
        bootstrap_unit = "bet"
        for start in range(0, n_boot, chunk):
            size = min(chunk, n_boot - start)
            idx = rng.integers(0, n, size=(size, n))
            boot_means[start : start + size] = clvs[idx].mean(axis=1)
    else:
        _, inverse = np.unique(group_arr, return_inverse=True)
        n_clusters = int(inverse.max()) + 1
        bootstrap_unit = "cluster"
        cluster_sums = np.bincount(inverse, weights=clvs)
        cluster_counts = np.bincount(inverse)
        for start in range(0, n_boot, chunk):
            size = min(chunk, n_boot - start)
            idx = rng.integers(
                0, n_clusters, size=(size, n_clusters)
            )
            sampled_sums = cluster_sums[idx].sum(axis=1)
            sampled_counts = cluster_counts[idx].sum(axis=1)
            boot_means[start : start + size] = sampled_sums / sampled_counts

    ci_low, ci_high = np.percentile(boot_means, [2.5, 97.5])
    return {
        "mean": float(clvs.mean()),
        "median": float(np.median(clvs)),
        "share_positive": float((clvs > 0).mean()),
        "ci_low": float(ci_low),
        "ci_high": float(ci_high),
        "n": n,
        "n_clusters": n_clusters,
        "bootstrap_unit": bootstrap_unit,
    }
