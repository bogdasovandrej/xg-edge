"""Tests for xgedge.decision.staking (self-contained fixtures)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from xgedge.decision.staking import (
    demargin_proportional,
    demargin_shin,
    ev,
    kelly_stake,
    simulate_bankroll,
)


def _bets(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "p_model", "odds", "won"])


def test_demargin_proportional_round_trip() -> None:
    true_p = np.array([0.5, 0.3, 0.2])
    odds = 1.0 / (true_p * 1.05)  # book with 5% margin
    out = demargin_proportional(odds)
    assert np.allclose(out, true_p, atol=1e-12)
    assert out.sum() == pytest.approx(1.0, abs=1e-12)


def test_demargin_shin_sums_to_one_and_favourite_longshot() -> None:
    odds = [1.3, 5.0, 9.5]  # asymmetric book, implied sum ~1.075
    shin = demargin_shin(odds)
    prop = demargin_proportional(odds)
    assert shin.sum() == pytest.approx(1.0, abs=1e-6)
    # Shin removes more margin from longshots: longest shot gets LOWER prob
    # than proportional, favourite gets HIGHER.
    assert shin[2] < prop[2]
    assert shin[0] > prop[0]


def test_demargin_shin_margin_free_book_falls_back_to_proportional() -> None:
    odds = [2.0, 2.0]  # no margin: no Shin root to bracket
    out = demargin_shin(odds)
    assert np.allclose(out, [0.5, 0.5], atol=1e-9)


def test_ev() -> None:
    assert ev(0.5, 2.2) == pytest.approx(0.1)
    assert ev(0.5, 2.0) == pytest.approx(0.0)


def test_kelly_zero_when_edge_nonpositive() -> None:
    assert kelly_stake(0.5, 2.0) == 0.0  # ev == 0
    assert kelly_stake(0.4, 2.0) == 0.0  # ev < 0


def test_kelly_clipped_to_cap() -> None:
    # raw fractional kelly = 0.25 * (0.6*2 - 1) / (2 - 1) = 0.05 > cap
    assert kelly_stake(0.6, 2.0, fraction=0.25, cap=0.02) == pytest.approx(0.02)


def test_kelly_formula_below_cap() -> None:
    p, odds, fraction = 0.55, 2.0, 0.25
    expected = fraction * (p * odds - 1.0) / (odds - 1.0)  # 0.025
    assert kelly_stake(p, odds, fraction=fraction, cap=1.0) == pytest.approx(expected)


def test_simulate_flat_hand_computed() -> None:
    bets = _bets(
        [
            ("2024-01-01", 0.6, 2.0, True),
            ("2024-01-02", 0.6, 3.0, False),
            ("2024-01-03", 0.6, 2.0, True),
        ]
    )
    out = simulate_bankroll(bets, staking="flat", flat_size=0.1)
    # 1.0 --stake .1 win @2.0--> 1.1 --stake .11 lose--> 0.99
    #     --stake .099 win @2.0--> 1.089
    assert out["final_bankroll"] == pytest.approx(1.089)
    assert out["total_staked"] == pytest.approx(0.309)
    assert out["roi"] == pytest.approx(0.089 / 0.309)
    assert out["n_bets"] == 3
    assert out["max_drawdown"] == pytest.approx((1.1 - 0.99) / 1.1)


def test_simulate_kelly_hand_computed() -> None:
    bets = _bets(
        [
            ("2024-01-01", 0.6, 2.0, True),
            ("2024-01-02", 0.6, 2.0, False),
            ("2024-01-03", 0.6, 2.0, True),
        ]
    )
    out = simulate_bankroll(bets, staking="kelly", fraction=0.25, cap=1.0)
    # stake fraction per bet = 0.25 * (0.6*2 - 1) / (2 - 1) = 0.05
    # 1.0 -> 1.05 -> 0.9975 -> 1.047375
    assert out["final_bankroll"] == pytest.approx(1.047375)
    assert out["total_staked"] == pytest.approx(0.05 + 0.0525 + 0.049875)
    assert out["n_bets"] == 3
    assert out["max_drawdown"] == pytest.approx((1.05 - 0.9975) / 1.05)


def test_simulate_kelly_skips_no_edge_bets() -> None:
    bets = _bets(
        [
            ("2024-01-01", 0.4, 2.0, True),  # no edge -> zero stake, skipped
            ("2024-01-02", 0.6, 2.0, True),
        ]
    )
    out = simulate_bankroll(bets, staking="kelly", fraction=0.25, cap=1.0)
    assert out["n_bets"] == 1
    assert out["final_bankroll"] == pytest.approx(1.05)


def test_simulate_settles_in_date_order() -> None:
    ordered = _bets(
        [
            ("2024-01-01", 0.6, 2.0, True),
            ("2024-01-02", 0.6, 3.0, False),
            ("2024-01-03", 0.6, 2.0, True),
        ]
    )
    shuffled = ordered.iloc[[2, 0, 1]].reset_index(drop=True)
    out_a = simulate_bankroll(ordered, staking="flat", flat_size=0.1)
    out_b = simulate_bankroll(shuffled, staking="flat", flat_size=0.1)
    assert out_a == out_b


def test_simulate_empty_bets() -> None:
    out = simulate_bankroll(_bets([]), staking="flat")
    assert out == {
        "final_bankroll": 1.0,
        "roi": 0.0,
        "max_drawdown": 0.0,
        "n_bets": 0,
        "total_staked": 0.0,
    }


def test_simulate_unknown_staking_raises() -> None:
    with pytest.raises(ValueError):
        simulate_bankroll(
            _bets([("2024-01-01", 0.6, 2.0, True)]), staking="martingale"
        )
