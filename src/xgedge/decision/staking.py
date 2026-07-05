"""De-margining bookmaker odds, expected value, and Kelly staking."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

# Required columns of the bets frame consumed by simulate_bankroll.
BET_COLS = ("date", "p_model", "odds", "won")


def demargin_proportional(odds: Sequence[float]) -> np.ndarray:
    """Implied probabilities 1/odds, normalized to sum to 1."""
    q = 1.0 / np.asarray(odds, dtype=float)
    return q / q.sum()


def _shin_probs(q: np.ndarray, z: float) -> np.ndarray:
    """Shin probabilities for insider share ``z`` given implied probs ``q``."""
    b = q.sum()
    return (np.sqrt(z * z + 4.0 * (1.0 - z) * q * q / b) - z) / (2.0 * (1.0 - z))


def demargin_shin(odds: Sequence[float]) -> np.ndarray:
    """Shin (1993) de-margined probabilities via bisection on the insider share z.

    Solves sum_i p_i(z) = 1 with
    p_i = (sqrt(z^2 + 4(1-z) q_i^2 / B) - z) / (2(1-z)), q_i = 1/o_i, B = sum q_i.
    Falls back to proportional de-margining when no root can be bracketed
    (e.g. a margin-free book) or the solution misses sum-to-1 by > 1e-6.
    """
    q = 1.0 / np.asarray(odds, dtype=float)
    lo, hi = 0.0, 1.0 - 1e-9
    f_lo = _shin_probs(q, lo).sum() - 1.0
    f_hi = _shin_probs(q, hi).sum() - 1.0
    if f_lo <= 0.0 or f_hi >= 0.0:
        return demargin_proportional(odds)
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _shin_probs(q, mid).sum() - 1.0 > 0.0:
            lo = mid
        else:
            hi = mid
    p = _shin_probs(q, 0.5 * (lo + hi))
    if abs(p.sum() - 1.0) > 1e-6:
        return demargin_proportional(odds)
    return p


def ev(p: float, odds: float) -> float:
    """Expected value per unit stake: p * odds - 1."""
    return p * odds - 1.0


def kelly_stake(p: float, odds: float, fraction: float = 0.25, cap: float = 0.02) -> float:
    """Fractional Kelly stake as a share of bankroll, clipped to [0, cap].

    Full Kelly is (p*odds - 1) / (odds - 1); returns 0 when the edge is <= 0.
    """
    edge = ev(p, odds)
    if edge <= 0.0 or odds <= 1.0:
        return 0.0
    return float(min(fraction * edge / (odds - 1.0), cap))


def simulate_bankroll(
    bets: pd.DataFrame,
    staking: str = "kelly",
    fraction: float = 0.25,
    cap: float = 0.02,
    flat_size: float = 0.01,
) -> dict:
    """Settle bets chronologically, compounding from bankroll 1.0.

    ``bets`` needs columns "date", "p_model", "odds", "won" (bool). Each stake
    is a fraction of the CURRENT bankroll: for "kelly" via
    :func:`kelly_stake`, for "flat" the constant ``flat_size``. Zero-stake
    bets (no Kelly edge) are skipped and excluded from ``n_bets``.
    ``max_drawdown`` is the largest relative peak-to-trough decline of the
    equity curve; ``roi`` is total pnl divided by total staked.
    """
    bankroll = 1.0
    peak = 1.0
    max_dd = 0.0
    total_staked = 0.0
    n_bets = 0
    for row in bets.sort_values("date", kind="stable").itertuples(index=False):
        odds = float(row.odds)
        if staking == "kelly":
            f = kelly_stake(float(row.p_model), odds, fraction=fraction, cap=cap)
        elif staking == "flat":
            f = flat_size
        else:
            raise ValueError(f"unknown staking scheme: {staking!r}")
        stake = f * bankroll
        if stake <= 0.0:
            continue
        total_staked += stake
        n_bets += 1
        if bool(row.won):
            bankroll += stake * (odds - 1.0)
        else:
            bankroll -= stake
        peak = max(peak, bankroll)
        max_dd = max(max_dd, (peak - bankroll) / peak)
    roi = (bankroll - 1.0) / total_staked if total_staked > 0.0 else 0.0
    return {
        "final_bankroll": float(bankroll),
        "roi": float(roi),
        "max_drawdown": float(max_dd),
        "n_bets": int(n_bets),
        "total_staked": float(total_staked),
    }
