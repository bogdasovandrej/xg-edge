"""Tests for the model layer: dixon_coles, poisson_glm, baselines.

Self-contained: all fixtures are synthetic DataFrames built inline.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import poisson

from xgedge.contracts import Col, Feat
from xgedge.models.baselines import GoalsAvgPoisson, UniformBaseline
from xgedge.models.dixon_coles import DixonColesClassic, fit_rho, score_matrix, tau
from xgedge.models.poisson_glm import PoissonGBMModel, PoissonGLMModel

# ---------------------------------------------------------------------------
# tau / score_matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lh,la,rho",
    [
        (1.4, 1.1, 0.0),
        (1.4, 1.1, -0.13),
        (2.3, 0.7, 0.1),
        (0.9, 0.9, -0.05),
        (1.0, 1.0, 0.18),
    ],
)
def test_score_matrix_sums_to_one(lh: float, la: float, rho: float) -> None:
    m = score_matrix(lh, la, rho=rho, max_goals=10)
    assert m.shape == (11, 11)
    assert (m >= 0).all()
    assert abs(m.sum() - 1.0) < 1e-9


def test_score_matrix_rho_zero_is_independent_poisson() -> None:
    lh, la, max_goals = 1.7, 1.2, 10
    goals = np.arange(max_goals + 1)
    outer = np.outer(poisson.pmf(goals, lh), poisson.pmf(goals, la))
    expected = outer / outer.sum()
    m = score_matrix(lh, la, rho=0.0, max_goals=max_goals)
    np.testing.assert_array_equal(m, expected)


@pytest.mark.parametrize("rho", [-0.15, -0.05, 0.0, 0.08, 0.2])
def test_tau_corrections_cancel_over_low_score_block(rho: float) -> None:
    # The DC corrections redistribute mass within the 2x2 low-score block
    # without changing its total probability.
    lh, la = 1.35, 1.05
    raw = corrected = 0.0
    for x in (0, 1):
        for y in (0, 1):
            p = poisson.pmf(x, lh) * poisson.pmf(y, la)
            raw += p
            corrected += p * tau(x, y, lh, la, rho)
    assert abs(corrected - raw) < 1e-12


def test_tau_outside_low_scores_is_one() -> None:
    assert tau(2, 0, 1.3, 1.1, -0.1) == 1.0
    assert tau(0, 2, 1.3, 1.1, -0.1) == 1.0
    assert tau(3, 4, 1.3, 1.1, 0.15) == 1.0


# ---------------------------------------------------------------------------
# fit_rho
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rho_true", [-0.1, 0.12])
def test_fit_rho_recovers_known_rho(rho_true: float) -> None:
    rng = np.random.default_rng(7)
    lh, la, max_goals, n = 1.35, 1.05, 8, 8000
    m = score_matrix(lh, la, rho=rho_true, max_goals=max_goals)
    flat_idx = rng.choice(m.size, size=n, p=m.ravel())
    goals_h = flat_idx // (max_goals + 1)
    goals_a = flat_idx % (max_goals + 1)
    rho_hat = fit_rho(np.full(n, lh), np.full(n, la), goals_h, goals_a)
    assert abs(rho_hat - rho_true) < 0.05


# ---------------------------------------------------------------------------
# DixonColesClassic
# ---------------------------------------------------------------------------


def _synthetic_dc_matches() -> tuple[pd.DataFrame, list, np.ndarray, float]:
    rng = np.random.default_rng(42)
    teams = [f"team_{i}" for i in range(6)]
    att_true = np.array([0.35, 0.2, 0.05, -0.05, -0.2, -0.35])
    deff_true = np.array([0.25, -0.1, 0.05, -0.05, 0.1, -0.25])
    mu_true, home_adv_true = 0.15, 0.3

    rows = []
    date = pd.Timestamp("2024-01-01")
    for _ in range(8):  # 8 double round-robins -> 240 matches
        for i in range(6):
            for j in range(6):
                if i == j:
                    continue
                lam_h = np.exp(mu_true + home_adv_true + att_true[i] - deff_true[j])
                lam_a = np.exp(mu_true + att_true[j] - deff_true[i])
                rows.append(
                    {
                        Col.DATE: date,
                        Col.HOME: teams[i],
                        Col.AWAY: teams[j],
                        Col.FTHG: int(rng.poisson(lam_h)),
                        Col.FTAG: int(rng.poisson(lam_a)),
                    }
                )
                date += pd.Timedelta(hours=6)
    return pd.DataFrame(rows), teams, att_true, home_adv_true


def test_dixon_coles_classic_recovers_structure() -> None:
    matches, teams, att_true, _ = _synthetic_dc_matches()
    model = DixonColesClassic().fit(matches, half_life_days=1e6)

    att_hat = np.array([model.att_[t] for t in teams])
    assert att_hat[0] > att_hat[5]  # strongest attack beats weakest
    assert np.corrcoef(att_true, att_hat)[0, 1] > 0.8
    assert model.home_adv_ > 0
    # mean-zero identifiability
    assert abs(att_hat.mean()) < 1e-8
    assert abs(np.mean(list(model.deff_.values()))) < 1e-8


def test_dixon_coles_classic_unknown_team_gets_league_average() -> None:
    matches, _, _, _ = _synthetic_dc_matches()
    model = DixonColesClassic().fit(matches, half_life_days=1e6)
    new = pd.DataFrame({Col.HOME: ["mystery_h"], Col.AWAY: ["mystery_a"]})
    lam_h, lam_a = model.predict_lambdas(new)
    assert lam_h[0] == pytest.approx(np.exp(model.mu_ + model.home_adv_))
    assert lam_a[0] == pytest.approx(np.exp(model.mu_))


# ---------------------------------------------------------------------------
# PoissonGLMModel / PoissonGBMModel
# ---------------------------------------------------------------------------


def _synthetic_features(n: int = 3000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    b0, b_att, b_def, b_home = -0.6, 0.7, 0.35, 0.25
    att_h = rng.uniform(0.6, 2.0, n)
    def_h = rng.uniform(0.6, 2.0, n)
    att_a = rng.uniform(0.6, 2.0, n)
    def_a = rng.uniform(0.6, 2.0, n)
    lam_h = np.exp(b0 + b_att * att_h + b_def * def_a + b_home)
    lam_a = np.exp(b0 + b_att * att_a + b_def * def_h)
    return pd.DataFrame(
        {
            Feat.ATT_H: att_h,
            Feat.DEF_H: def_h,
            Feat.ATT_A: att_a,
            Feat.DEF_A: def_a,
            Col.FTHG: rng.poisson(lam_h),
            Col.FTAG: rng.poisson(lam_a),
        }
    )


def test_poisson_glm_learns_positive_attack_effect() -> None:
    feats = _synthetic_features()
    model = PoissonGLMModel().fit(feats)
    # params_ = [b0, b_att, b_def_opp, b_is_home]
    assert model.params_[1] > 0
    assert model.params_[1] == pytest.approx(0.7, abs=0.15)

    lam_h, lam_a = model.predict_lambdas(feats)
    assert np.isfinite(lam_h).all() and np.isfinite(lam_a).all()
    assert (lam_h > 0).all() and (lam_a > 0).all()


def test_poisson_glm_nan_rows_get_fallback_lambdas() -> None:
    feats = _synthetic_features()
    model = PoissonGLMModel().fit(feats)

    test = feats.head(4).copy()
    test.loc[test.index[:2], Feat.ATT_H] = np.nan  # breaks home side only
    test.loc[test.index[2], Feat.DEF_H] = np.nan  # breaks away side only
    lam_h, lam_a = model.predict_lambdas(test)

    assert lam_h[0] == pytest.approx(model.fallback_lambda_)
    assert lam_h[1] == pytest.approx(model.fallback_lambda_)
    assert lam_a[2] == pytest.approx(model.fallback_lambda_)
    # untouched sides still model-based
    assert np.isfinite(lam_a[:2]).all() and (lam_a[:2] > 0).all()
    assert np.isfinite(lam_h[3]) and lam_h[3] > 0
    assert (lam_h > 0).all() and (lam_a > 0).all()


def test_poisson_gbm_fits_and_handles_nan() -> None:
    feats = _synthetic_features(n=1500, seed=1)
    model = PoissonGBMModel().fit(feats)
    lam_h, lam_a = model.predict_lambdas(feats)
    assert np.isfinite(lam_h).all() and np.isfinite(lam_a).all()
    assert (lam_h > 0).all() and (lam_a > 0).all()
    # higher att_h with same defence should predict more goals on average
    order = np.argsort(feats[Feat.ATT_H].to_numpy())
    assert lam_h[order[-200:]].mean() > lam_h[order[:200]].mean()

    test = feats.head(2).copy()
    test.loc[test.index[0], Feat.ATT_H] = np.nan
    lam_h2, _ = model.predict_lambdas(test)
    assert lam_h2[0] == pytest.approx(model.fallback_lambda_)


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


def test_uniform_baseline_predicts_thirds() -> None:
    matches = pd.DataFrame({Col.HOME: ["a", "b"], Col.AWAY: ["b", "a"]})
    p = UniformBaseline().fit(matches).predict_1x2(matches)
    assert p.shape == (2, 3)
    np.testing.assert_allclose(p, 1.0 / 3.0)


def _goals_avg_fixture() -> pd.DataFrame:
    # Team "a" scores 3 per match, everyone else 1 -> league avg per
    # team-match is 1.5, so a's att_factor is exactly 2.0.
    rows = []
    for opp in ("b", "c", "d"):
        rows.append((("a"), opp, 3, 1))
        rows.append((opp, "a", 1, 3))
    for home, away in (("b", "c"), ("c", "b"), ("b", "d"), ("d", "b"), ("c", "d"), ("d", "c")):
        rows.append((home, away, 1, 1))
    return pd.DataFrame(rows, columns=[Col.HOME, Col.AWAY, Col.FTHG, Col.FTAG])


def test_goals_avg_poisson_att_factor() -> None:
    model = GoalsAvgPoisson().fit(_goals_avg_fixture())
    assert model.att_factor_["a"] == pytest.approx(2.0)
    assert model.att_factor_["b"] == pytest.approx(1.0 / 1.5)


def test_goals_avg_poisson_lambdas_and_unseen_team() -> None:
    matches = _goals_avg_fixture()
    model = GoalsAvgPoisson().fit(matches)

    pred = pd.DataFrame({Col.HOME: ["a"], Col.AWAY: ["b"]})
    lam_h, lam_a = model.predict_lambdas(pred)
    exp_lam_h = model.league_home_avg_ * model.att_factor_["a"] * model.def_factor_["b"]
    exp_lam_a = model.league_away_avg_ * model.att_factor_["b"] * model.def_factor_["a"]
    assert lam_h[0] == pytest.approx(exp_lam_h)
    assert lam_a[0] == pytest.approx(exp_lam_a)

    unseen = pd.DataFrame({Col.HOME: ["zzz"], Col.AWAY: ["a"]})
    lam_h2, lam_a2 = model.predict_lambdas(unseen)
    # unseen home team: both its factors default to 1.0
    assert lam_h2[0] == pytest.approx(model.league_home_avg_ * model.def_factor_["a"])
    assert lam_a2[0] == pytest.approx(model.league_away_avg_ * model.att_factor_["a"])
