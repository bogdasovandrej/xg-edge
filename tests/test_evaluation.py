"""Tests for the evaluation layer: splits, metrics, calibration, CLV, report."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from xgedge.evaluation.calibration import plot_reliability, reliability_table
from xgedge.evaluation.clv import clv_per_bet, summarize_clv
from xgedge.evaluation.metrics import (
    brier_1x2,
    brier_binary,
    logloss_1x2,
    logloss_binary,
)
from xgedge.evaluation.report import write_metrics_json, write_summary_md
from xgedge.evaluation.walkforward import walk_forward_splits


# ---------------------------------------------------------------------------
# walk_forward_splits
# ---------------------------------------------------------------------------

def test_walkforward_train_strictly_before_test():
    dates = pd.Series(pd.date_range("2023-01-01", periods=365, freq="D"))
    splits = list(
        walk_forward_splits(dates, "2023-07-01", step_days=30, min_train=10)
    )
    assert len(splits) > 0
    for train_idx, test_idx in splits:
        assert dates.iloc[train_idx].max() < dates.iloc[test_idx].min()


def test_walkforward_test_windows_cover_all_dates_after_cutoff():
    dates = pd.Series(pd.date_range("2023-01-01", periods=365, freq="D"))
    cutoff = pd.Timestamp("2023-07-01")
    splits = list(
        walk_forward_splits(dates, cutoff, step_days=30, min_train=5)
    )
    covered = np.concatenate([test for _, test in splits])
    expected = np.flatnonzero(dates.to_numpy() >= cutoff.to_datetime64())
    assert sorted(covered.tolist()) == expected.tolist()
    # no position is tested twice
    assert len(covered) == len(set(covered.tolist()))


def test_walkforward_respects_min_train():
    dates = pd.Series(pd.date_range("2023-01-01", periods=100, freq="D"))
    # cutoff leaves only 10 train rows; windows advance 10 days at a time,
    # so the first two windows (train sizes 10 and 20) must be skipped
    splits = list(
        walk_forward_splits(dates, "2023-01-11", step_days=10, min_train=30)
    )
    assert len(splits) > 0
    for train_idx, _ in splits:
        assert train_idx.size >= 30
    first_test_dates = dates.iloc[splits[0][1]]
    assert first_test_dates.min() == pd.Timestamp("2023-01-31")


def test_walkforward_indices_positional_on_unsorted_series():
    base = pd.Series(pd.date_range("2023-01-01", periods=200, freq="D"))
    shuffled = base.sample(frac=1, random_state=42)  # unsorted, non-default index
    splits = list(
        walk_forward_splits(shuffled, "2023-04-01", step_days=30, min_train=10)
    )
    assert len(splits) > 0
    covered = []
    for train_idx, test_idx in splits:
        # positional access must still give a clean chronological separation
        assert shuffled.iloc[train_idx].max() < shuffled.iloc[test_idx].min()
        covered.extend(test_idx.tolist())
    expected = np.flatnonzero(
        shuffled.to_numpy() >= np.datetime64("2023-04-01")
    )
    assert sorted(covered) == expected.tolist()


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def test_brier_1x2_hand_computed():
    p = np.array([[0.5, 0.3, 0.2], [0.1, 0.2, 0.7]])
    y = ["H", "A"]
    # match 1: (0.5-1)^2 + 0.3^2 + 0.2^2 = 0.38
    # match 2: 0.1^2 + 0.2^2 + (0.7-1)^2 = 0.14
    assert brier_1x2(p, y) == pytest.approx(0.26)


def test_brier_1x2_perfect_and_uniform():
    y = ["H", "D", "A"]
    perfect = np.eye(3)
    assert brier_1x2(perfect, y) == pytest.approx(0.0)
    uniform = np.full((3, 3), 1.0 / 3.0)
    assert brier_1x2(uniform, y) == pytest.approx(2.0 / 3.0)


def test_logloss_clipping_finite_on_zero_prob():
    p = np.array([[0.0, 0.5, 0.5]])
    value = logloss_1x2(p, ["H"])
    assert np.isfinite(value)
    assert value == pytest.approx(-np.log(1e-12))

    bin_value = logloss_binary(np.array([0.0, 1.0]), np.array([1.0, 0.0]))
    assert np.isfinite(bin_value)


def test_binary_metrics_values():
    p = np.array([0.8, 0.3])
    y = np.array([1.0, 0.0])
    assert brier_binary(p, y) == pytest.approx((0.04 + 0.09) / 2)
    expected_ll = -(np.log(0.8) + np.log(0.7)) / 2
    assert logloss_binary(p, y) == pytest.approx(expected_ll)


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------

def test_reliability_table_calibrated_synthetic():
    rng = np.random.default_rng(0)
    n = 20000
    p = rng.uniform(0.0, 1.0, n)
    y = (rng.uniform(0.0, 1.0, n) < p).astype(float)
    table = reliability_table(p, y, n_bins=10)

    assert list(table.columns) == ["bin_mid", "p_mean", "y_rate", "count"]
    assert int(table["count"].sum()) == n
    np.testing.assert_allclose(table["p_mean"], table["y_rate"], atol=0.03)


def test_reliability_table_drops_empty_bins():
    p = np.array([0.05, 0.06, 0.95])  # only first and last bins populated
    y = np.array([0.0, 1.0, 1.0])
    table = reliability_table(p, y, n_bins=10)
    assert len(table) == 2
    assert table["bin_mid"].tolist() == [0.05, 0.95]
    assert table["count"].tolist() == [2, 1]


def test_plot_reliability_writes_file(tmp_path):
    rng = np.random.default_rng(1)
    p = rng.uniform(0.0, 1.0, 500)
    y = (rng.uniform(0.0, 1.0, 500) < p).astype(float)
    tables = {"model_a": reliability_table(p, y), "model_b": reliability_table(p, y)}
    out = tmp_path / "plots" / "reliability.png"
    plot_reliability(tables, out, title="Calibration")
    assert out.exists()
    assert out.stat().st_size > 0


# ---------------------------------------------------------------------------
# clv
# ---------------------------------------------------------------------------

def test_clv_per_bet_formula():
    taken = np.array([2.0, 3.0, 1.5])
    fair = np.array([0.55, 0.30, 2.0 / 3.0])
    np.testing.assert_allclose(clv_per_bet(taken, fair), [0.1, -0.1, 0.0])


def test_summarize_clv_ci_and_share_positive():
    rng = np.random.default_rng(4)
    true_mean = 0.02
    clvs = rng.normal(true_mean, 0.05, 2000)
    summary = summarize_clv(clvs, n_boot=2000, seed=0)

    assert summary["n"] == 2000
    assert summary["mean"] == pytest.approx(clvs.mean())
    assert summary["median"] == pytest.approx(np.median(clvs))
    assert summary["share_positive"] == pytest.approx((clvs > 0).mean())
    assert summary["ci_low"] < true_mean < summary["ci_high"]
    assert summary["ci_low"] < summary["mean"] < summary["ci_high"]


def test_summarize_clv_deterministic():
    clvs = np.linspace(-0.05, 0.10, 40)
    a = summarize_clv(clvs, n_boot=500, seed=3)
    b = summarize_clv(clvs, n_boot=500, seed=3)
    assert a == b


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def test_write_metrics_json_handles_numpy_types(tmp_path):
    results = {
        "models_1x2": {
            "glm_dc": {"brier": np.float64(0.2011), "n": np.int32(310)},
        },
        "flags": np.array([True, False]),
        "seed_ok": np.bool_(True),
    }
    path = tmp_path / "metrics.json"
    write_metrics_json(results, path)

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["models_1x2"]["glm_dc"]["brier"] == pytest.approx(0.2011)
    assert loaded["models_1x2"]["glm_dc"]["n"] == 310
    assert loaded["flags"] == [True, False]
    assert loaded["seed_ok"] is True


def test_write_summary_md_smoke(tmp_path):
    results = {
        "config": {"initial_train_end": "2023-07-01", "step_days": 30},
        "models_1x2": {
            "glm_dc": {"brier": 0.2011, "logloss": 0.98765},
            "uniform": {"brier": 2.0 / 3.0, "logloss": np.log(3.0)},
        },
        "totals": {"glm_dc": {"brier": 0.24}},
        "bankroll": {
            "kelly": {"final_bankroll": 1.12, "roi": 0.031},
            "flat": {"final_bankroll": 1.05, "roi": 0.012},
        },
        "clv": {"mean": 0.011, "share_positive": 0.58, "n": 120},
    }
    path = tmp_path / "summary.md"
    write_summary_md(results, path)

    text = path.read_text(encoding="utf-8")
    assert "glm_dc" in text
    assert "uniform" in text
    assert "2023-07-01" in text
    assert "0.9877" in text  # floats rounded to 4 decimals


def test_write_summary_md_tolerates_missing_blocks(tmp_path):
    path = tmp_path / "summary_min.md"
    write_summary_md({"models_1x2": {"glm_dc": {"brier": 0.2}}}, path)
    text = path.read_text(encoding="utf-8")
    assert "glm_dc" in text
    assert "Betting simulation" not in text
