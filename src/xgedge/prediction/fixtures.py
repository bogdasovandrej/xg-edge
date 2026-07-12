"""Predictions for scheduled fixtures with no result fields.

For every kickoff day the model and its pre-match features are rebuilt using
only completed matches from earlier calendar days. The cleaned historical
dataset currently has dates but no dependable kickoff times, so excluding the
whole kickoff day is the only defensible no-leak boundary.
"""
from __future__ import annotations

import json
from typing import Optional

import numpy as np
import pandas as pd

from xgedge.contracts import FIXTURE_COLUMNS, FIXTURE_RESULT_COLUMNS, Col, Feat, Pred
from xgedge.features.builder import build_features
from xgedge.markets.markets import prob_btts, prob_over, probs_1x2, top_scores
from xgedge.models.baselines import GoalsAvgPoisson
from xgedge.models.dixon_coles import DixonColesClassic, fit_rho, score_matrix
from xgedge.models.poisson_glm import PoissonGBMModel, PoissonGLMModel
from xgedge.pipeline import DEFAULT_FEATURE_PARAMS

SUPPORTED_MODELS = ("glm_dc", "gbm_dc", "dc_classic", "goals_poisson")
_HISTORY_REQUIRED = (
    Col.MATCH_ID,
    Col.SEASON,
    Col.DATE,
    Col.HOME,
    Col.AWAY,
    Col.FTHG,
    Col.FTAG,
    Col.FTR,
    Col.XG_H,
    Col.XG_A,
    Col.RED_H,
    Col.RED_A,
)
_FEATURE_COLS = (Feat.ATT_H, Feat.DEF_H, Feat.ATT_A, Feat.DEF_A)


def _utc_naive(values: pd.Series, label: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce", utc=True)
    if parsed.isna().any():
        bad = values[parsed.isna()].astype(str).head(3).tolist()
        raise ValueError(f"{label} contains invalid dates: {bad}")
    return parsed.dt.tz_convert(None)


def validate_fixtures(fixtures: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize the result-free fixture DataFrame.

    Returns a defensive copy with ``date`` converted to a timezone-naive UTC
    ``datetime64[ns]`` series. Populated result columns are rejected so this
    contract cannot accidentally be used as a backtest input.
    """
    if not isinstance(fixtures, pd.DataFrame):
        raise TypeError("fixtures must be a pandas DataFrame")
    if fixtures.empty:
        raise ValueError("fixtures must contain at least one row")
    missing = [column for column in FIXTURE_COLUMNS if column not in fixtures.columns]
    if missing:
        raise ValueError(f"fixtures missing required columns: {missing}")

    out = fixtures.copy()
    for column in FIXTURE_RESULT_COLUMNS:
        if column in out.columns and out[column].notna().any():
            raise ValueError(f"future fixtures must not contain results in {column!r}")

    if out[list(FIXTURE_COLUMNS)].isna().any().any():
        raise ValueError("fixture contract columns must not contain null values")
    for column in (Col.MATCH_ID, Col.SEASON, Col.HOME, Col.AWAY):
        out[column] = out[column].astype(str).str.strip()
        if out[column].eq("").any():
            raise ValueError(f"fixture column {column!r} must contain non-empty strings")
    if out[Col.MATCH_ID].duplicated().any():
        duplicate = out.loc[out[Col.MATCH_ID].duplicated(), Col.MATCH_ID].iloc[0]
        raise ValueError(f"fixture match_id must be unique; duplicate {duplicate!r}")
    if out[Col.HOME].eq(out[Col.AWAY]).any():
        raise ValueError("fixture home and away teams must be different")

    out[Col.DATE] = _utc_naive(out[Col.DATE], "fixtures")
    return out


def _validate_history(matches: pd.DataFrame, *, use_npxg: bool) -> pd.DataFrame:
    if not isinstance(matches, pd.DataFrame):
        raise TypeError("matches must be a pandas DataFrame")
    if matches.empty:
        raise ValueError("historical matches must contain at least one row")
    required = list(_HISTORY_REQUIRED)
    if use_npxg:
        required.extend([Col.NPXG_H, Col.NPXG_A])
    missing = [column for column in required if column not in matches.columns]
    if missing:
        raise ValueError(f"historical matches missing required columns: {missing}")

    out = matches.copy()
    if out[required].isna().any().any():
        raise ValueError("historical matches contain nulls in required training columns")
    out[Col.DATE] = _utc_naive(out[Col.DATE], "historical matches")
    if out[Col.MATCH_ID].duplicated().any():
        raise ValueError("historical match_id values must be unique")
    for column in (Col.MATCH_ID, Col.SEASON, Col.HOME, Col.AWAY):
        valid_strings = out[column].map(
            lambda value: isinstance(value, str) and bool(value.strip())
        )
        if not valid_strings.all():
            raise ValueError(f"historical {column} must contain non-empty strings")
    if out[Col.HOME].eq(out[Col.AWAY]).any():
        raise ValueError("historical home and away teams must be different")
    if not out[Col.FTR].isin(("H", "D", "A")).all():
        raise ValueError("historical ftr values must be H, D or A")
    goals: dict[str, np.ndarray] = {}
    for column in (Col.FTHG, Col.FTAG):
        values = pd.to_numeric(out[column], errors="coerce").to_numpy(float)
        if not np.isfinite(values).all() or np.any(values < 0) or np.any(values % 1):
            raise ValueError(f"historical {column} must contain non-negative integers")
        goals[column] = values
    expected_ftr = np.where(
        goals[Col.FTHG] > goals[Col.FTAG],
        "H",
        np.where(goals[Col.FTHG] < goals[Col.FTAG], "A", "D"),
    )
    if not np.array_equal(out[Col.FTR].to_numpy(), expected_ftr):
        raise ValueError("historical ftr is inconsistent with full-time goals")
    xg_columns = [Col.XG_H, Col.XG_A]
    if use_npxg:
        xg_columns.extend([Col.NPXG_H, Col.NPXG_A])
    for column in xg_columns:
        values = pd.to_numeric(out[column], errors="coerce").to_numpy(float)
        if not np.isfinite(values).all() or np.any(values < 0):
            raise ValueError(f"historical {column} must contain finite non-negative values")
    return out.sort_values(Col.DATE, kind="mergesort").reset_index(drop=True)


def _placeholder_rows(fixtures: pd.DataFrame) -> pd.DataFrame:
    """Add columns needed by ``build_features`` without fabricating results."""
    out = fixtures.copy()
    for column in (
        Col.FTHG,
        Col.FTAG,
        Col.FTR,
        Col.XG_H,
        Col.XG_A,
        Col.NPXG_H,
        Col.NPXG_A,
    ):
        out[column] = np.nan
    out[Col.RED_H] = 0
    out[Col.RED_A] = 0
    return out


def _fit_model(name: str, train: pd.DataFrame):
    if name == "glm_dc":
        return PoissonGLMModel().fit(train)
    if name == "gbm_dc":
        return PoissonGBMModel().fit(train)
    if name == "dc_classic":
        return DixonColesClassic().fit(train)
    if name == "goals_poisson":
        return GoalsAvgPoisson().fit(train)
    raise ValueError(f"unknown model {name!r}; expected one of {SUPPORTED_MODELS}")


def predict_fixtures(
    matches: pd.DataFrame,
    fixtures: pd.DataFrame,
    *,
    model: str = "glm_dc",
    feature_params: Optional[dict] = None,
    max_goals: int = 10,
    top_k: int = 5,
    force_rho_zero: bool = False,
) -> pd.DataFrame:
    """Fit causally and predict 1X2, O/U 2.5, BTTS and exact scores.

    A separate fit is performed for each fixture day. Only completed matches
    on earlier UTC calendar days are eligible for both feature construction
    and model fitting. Teams unseen before their fixture are rejected instead
    of silently receiving a misleading league-average prediction.
    """
    if model not in SUPPORTED_MODELS:
        raise ValueError(f"unknown model {model!r}; expected one of {SUPPORTED_MODELS}")
    if isinstance(top_k, bool) or not isinstance(top_k, (int, np.integer)) or top_k < 1:
        raise ValueError("top_k must be a positive integer")
    if (
        isinstance(max_goals, bool)
        or not isinstance(max_goals, (int, np.integer))
        or max_goals < 1
    ):
        raise ValueError("max_goals must be an integer of at least 1")

    params = {**DEFAULT_FEATURE_PARAMS, **dict(feature_params or {})}
    history = _validate_history(matches, use_npxg=bool(params["use_npxg"]))
    future = validate_fixtures(fixtures)
    overlap = set(history[Col.MATCH_ID]) & set(future[Col.MATCH_ID])
    if overlap:
        raise ValueError(f"fixture match_id already exists in history: {sorted(overlap)[0]!r}")

    future = future.copy()
    future["_fixture_order"] = np.arange(len(future))
    future["_cutoff"] = future[Col.DATE].dt.normalize()
    output: list[dict] = []

    for cutoff, batch in future.groupby("_cutoff", sort=True):
        eligible = history.loc[history[Col.DATE] < cutoff].copy()
        if eligible.empty:
            raise ValueError(f"no historical matches before fixture cutoff {cutoff}")
        known = set(eligible[Col.HOME]) | set(eligible[Col.AWAY])
        requested = set(batch[Col.HOME]) | set(batch[Col.AWAY])
        unknown = sorted(requested - known)
        if unknown:
            raise ValueError(
                f"teams unseen before fixture cutoff {cutoff.date()}: {unknown}"
            )

        clean_batch = batch.drop(columns=["_fixture_order", "_cutoff"])
        combined = pd.concat(
            [eligible, _placeholder_rows(clean_batch)], ignore_index=True, sort=False
        )
        all_features = build_features(combined, **params)
        fixture_ids = set(clean_batch[Col.MATCH_ID])
        train = all_features.loc[
            ~all_features[Col.MATCH_ID].isin(fixture_ids)
            & all_features[Feat.IS_VALID]
            & all_features[list(_FEATURE_COLS)].notna().all(axis=1)
        ].copy()
        target = all_features.loc[all_features[Col.MATCH_ID].isin(fixture_ids)].copy()
        target = target.set_index(Col.MATCH_ID).loc[clean_batch[Col.MATCH_ID]].reset_index()
        if train.empty:
            raise ValueError(
                f"no feature-valid training matches before fixture cutoff {cutoff.date()}"
            )

        fitted = _fit_model(model, train)
        if force_rho_zero or model == "goals_poisson":
            rho = 0.0
        else:
            train_lh, train_la = fitted.predict_lambdas(train)
            rho = fit_rho(
                train_lh,
                train_la,
                train[Col.FTHG].to_numpy(),
                train[Col.FTAG].to_numpy(),
            )
        lam_h, lam_a = fitted.predict_lambdas(target)
        train_end = train[Col.DATE].max()

        orders = dict(zip(batch[Col.MATCH_ID], batch["_fixture_order"]))
        for i, fixture in target.iterrows():
            matrix = score_matrix(
                float(lam_h[i]), float(lam_a[i]), rho=rho, max_goals=max_goals
            )
            p_home, p_draw, p_away = probs_1x2(matrix)
            ranked = top_scores(matrix, k=min(top_k, matrix.size))
            exact = [
                {"score": f"{home}-{away}", "probability": probability}
                for (home, away), probability in ranked
            ]
            (top_home, top_away), p_top = ranked[0]
            p_over25 = prob_over(matrix, 2.5)
            p_btts = prob_btts(matrix)
            output.append({
                "_fixture_order": orders[fixture[Col.MATCH_ID]],
                Col.MATCH_ID: fixture[Col.MATCH_ID],
                Col.SEASON: fixture[Col.SEASON],
                Col.DATE: fixture[Col.DATE],
                Col.HOME: fixture[Col.HOME],
                Col.AWAY: fixture[Col.AWAY],
                Pred.MODEL: model,
                Pred.TRAIN_MATCHES: int(len(train)),
                Pred.TRAIN_END: train_end,
                Feat.N_HIST_H: int(fixture[Feat.N_HIST_H]),
                Feat.N_HIST_A: int(fixture[Feat.N_HIST_A]),
                Feat.IS_VALID: bool(fixture[Feat.IS_VALID]),
                Pred.LAMBDA_H: float(lam_h[i]),
                Pred.LAMBDA_A: float(lam_a[i]),
                Pred.RHO: float(rho),
                Pred.P_HOME: p_home,
                Pred.P_DRAW: p_draw,
                Pred.P_AWAY: p_away,
                Pred.P_OVER25: p_over25,
                Pred.P_UNDER25: 1.0 - p_over25,
                Pred.P_BTTS: p_btts,
                Pred.P_NO_BTTS: 1.0 - p_btts,
                Pred.TOP_SCORE: f"{top_home}-{top_away}",
                Pred.P_TOP_SCORE: p_top,
                Pred.EXACT_SCORES: json.dumps(exact, separators=(",", ":")),
            })

    return (
        pd.DataFrame(output)
        .sort_values("_fixture_order", kind="stable")
        .drop(columns="_fixture_order")
        .reset_index(drop=True)
    )
