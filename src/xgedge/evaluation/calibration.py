"""Reliability (calibration) diagnostics for probability forecasts."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend; must be set before pyplot import
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def reliability_table(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """Bin predictions into uniform bins on [0, 1] and compare to outcomes.

    Returns a DataFrame with columns ``bin_mid`` (bin centre), ``p_mean``
    (mean predicted probability in the bin), ``y_rate`` (empirical hit rate)
    and ``count``. Empty bins are dropped.
    """
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    # interior edges -> bin ids 0..n_bins-1, left-closed; p == 1.0 lands in the last bin
    bin_ids = np.digitize(p, edges[1:-1])

    rows = []
    for b in range(n_bins):
        mask = bin_ids == b
        if not mask.any():
            continue
        rows.append(
            {
                "bin_mid": (edges[b] + edges[b + 1]) / 2.0,
                "p_mean": float(p[mask].mean()),
                "y_rate": float(y[mask].mean()),
                "count": int(mask.sum()),
            }
        )
    return pd.DataFrame(rows, columns=["bin_mid", "p_mean", "y_rate", "count"])


def plot_reliability(
    tables: dict[str, pd.DataFrame], out_path: Path, title: str = ""
) -> None:
    """Save a reliability plot with one panel per named table.

    Each panel shows p_mean vs y_rate with the diagonal as the perfect-
    calibration reference; marker area scales with the bin count.
    """
    n_panels = max(len(tables), 1)
    fig, axes = plt.subplots(
        1, n_panels, figsize=(4.0 * n_panels, 4.0), squeeze=False, sharey=True
    )
    for ax, (name, table) in zip(axes[0], tables.items()):
        ax.plot([0, 1], [0, 1], linestyle="--", color="grey", linewidth=1)
        if len(table) > 0:
            max_count = max(int(table["count"].max()), 1)
            sizes = 20.0 + 180.0 * table["count"].to_numpy() / max_count
            ax.scatter(table["p_mean"], table["y_rate"], s=sizes, alpha=0.7)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xlabel("Mean predicted probability")
        ax.set_ylabel("Empirical rate")
        ax.set_title(name)
    if title:
        fig.suptitle(title)
    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
