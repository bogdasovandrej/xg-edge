"""End-to-end smoke test of run_walkforward_eval on synthetic matches."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from xgedge.contracts import Col
from xgedge.models.dixon_coles import score_matrix
from xgedge.markets.markets import prob_over, probs_1x2
from xgedge.pipeline import run_walkforward_eval


def _synthetic_matches(n_per_season: int = 120, seed: int = 7) -> pd.DataFrame:
    """Three synthetic 'seasons' of matches with strengths, xG and odds."""
    rng = np.random.default_rng(seed)
    teams = [f"team_{i:02d}" for i in range(20)]
    strength = {t: rng.normal(0.0, 0.25) for t in teams}
    rows = []
    for season_i, year in enumerate([2020, 2021, 2022]):
        start = pd.Timestamp(f"{year}-08-01")
        for k in range(n_per_season):
            home, away = rng.choice(teams, size=2, replace=False)
            date = start + pd.Timedelta(days=int(k * 270 / n_per_season))
            lam_h = 1.45 * np.exp(strength[home] - strength[away] + 0.20)
            lam_a = 1.15 * np.exp(strength[away] - strength[home])
            gh = rng.poisson(lam_h)
            ga = rng.poisson(lam_a)
            xg_h = max(0.05, lam_h + rng.normal(0.0, 0.3))
            xg_a = max(0.05, lam_a + rng.normal(0.0, 0.3))

            m = score_matrix(lam_h, lam_a, 0.0)
            ph, pdr, pa = probs_1x2(m)
            p_over = prob_over(m, 2.5)
            margin = 1.05
            o = {
                "h": 1.0 / (ph * margin), "d": 1.0 / (pdr * margin),
                "a": 1.0 / (pa * margin),
                "over": 1.0 / (p_over * margin),
                "under": 1.0 / ((1.0 - p_over) * margin),
            }
            drift = 1.0 + rng.normal(0.0, 0.02)
            missing_odds = rng.random() < 0.03
            rows.append({
                Col.MATCH_ID: f"s{season_i}_{k}_{home}_{away}",
                Col.SEASON: f"20{20 + season_i}-{21 + season_i}",
                Col.DATE: date, Col.HOME: home, Col.AWAY: away,
                Col.FTHG: gh, Col.FTAG: ga,
                Col.FTR: "H" if gh > ga else ("A" if ga > gh else "D"),
                Col.XG_H: xg_h, Col.XG_A: xg_a,
                Col.NPXG_H: xg_h * 0.9, Col.NPXG_A: xg_a * 0.9,
                Col.PPDA_H: 10.0, Col.PPDA_A: 10.0,
                Col.DEEP_H: 5, Col.DEEP_A: 5,
                Col.RED_H: int(rng.random() < 0.03),
                Col.RED_A: int(rng.random() < 0.03),
                Col.B365H: np.nan if missing_odds else o["h"],
                Col.B365D: np.nan if missing_odds else o["d"],
                Col.B365A: np.nan if missing_odds else o["a"],
                Col.PSH: o["h"], Col.PSD: o["d"], Col.PSA: o["a"],
                Col.B365CH: o["h"] * drift, Col.B365CD: o["d"] * drift,
                Col.B365CA: o["a"] * drift,
                Col.PSCH: np.nan if missing_odds else o["h"] * drift,
                Col.PSCD: np.nan if missing_odds else o["d"] * drift,
                Col.PSCA: np.nan if missing_odds else o["a"] * drift,
                Col.B365_O25: o["over"], Col.B365_U25: o["under"],
                Col.B365C_O25: o["over"] * drift,
                Col.B365C_U25: o["under"] * drift,
                Col.P_O25: o["over"], Col.P_U25: o["under"],
                Col.PC_O25: (
                    np.nan if missing_odds else o["over"] * drift
                ),
                Col.PC_U25: (
                    np.nan if missing_odds else o["under"] * drift
                ),
            })
    return pd.DataFrame(rows).sort_values(Col.DATE).reset_index(drop=True)


@pytest.fixture(scope="module")
def results() -> dict:
    matches = _synthetic_matches()
    return run_walkforward_eval(
        matches, initial_train_end="2022-08-01", step_days=30
    )


def test_contract_keys(results: dict) -> None:
    for key in ["models_1x2", "totals", "predictions", "bets", "config"]:
        assert key in results


def test_probs_sum_to_one(results: dict) -> None:
    pred = results["predictions"]
    for m in ["glm_dc", "gbm_dc", "dc_classic", "goals_poisson", "uniform"]:
        p = pred[[f"{m}_ph", f"{m}_pd", f"{m}_pa"]].dropna().to_numpy()
        assert len(p) > 0
        np.testing.assert_allclose(p.sum(axis=1), 1.0, atol=1e-6)


def test_no_predictions_before_train_end(results: dict) -> None:
    assert (results["predictions"][Col.DATE] >= pd.Timestamp("2022-08-01")).all()


def test_stakes_capped(results: dict) -> None:
    bets = results["bets"]
    assert len(bets) > 0
    assert (bets["stake"] <= 0.02 + 1e-12).all()
    assert (bets["stake"] >= 0.0).all()


def test_metrics_finite_and_uniform_brier(results: dict) -> None:
    for m, entry in results["models_1x2"].items():
        assert np.isfinite(entry["brier"]), m
        assert np.isfinite(entry["logloss"]), m
    assert results["models_1x2"]["uniform"]["brier"] == pytest.approx(
        2.0 / 3.0, abs=0.02
    )


def test_market_only_where_closing_exists(results: dict) -> None:
    pred = results["predictions"]
    n_market = results["models_1x2"]["market"]["n"]
    assert 0 < n_market <= len(pred)
    assert results["models_1x2"]["market"]["n_common"] == n_market
    # market predictions must be NaN exactly where closing odds were NaN
    assert pred["market_ph"].isna().sum() == len(pred) - n_market
    assert results["totals"]["market"]["n_common"] == results["totals"]["market"]["n"]


def test_effective_feature_config_is_locked_and_reported(results: dict) -> None:
    config = results["config"]
    assert config["feature_half_life_days"] == 180.0
    assert config["feature_adjust_opponent"] is False
    assert config["feature_use_npxg"] is False
    assert config["feature_decay"] is True
    assert config["feature_min_history"] == 5
    assert config["feature_venue_blend"] == 0.3



def test_force_rho_zero_variant() -> None:
    matches = _synthetic_matches(n_per_season=110, seed=11)
    res = run_walkforward_eval(
        matches,
        feature_params={"force_rho_zero": True},
        initial_train_end="2022-08-01",
        models=["glm_dc"],
    )
    assert res["config"]["force_rho_zero"] is True
    assert np.isfinite(res["models_1x2"]["glm_dc"]["logloss"])
