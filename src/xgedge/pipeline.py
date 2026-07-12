"""Walk-forward evaluation pipeline: features -> models -> markets -> bets.

Orchestrates the whole modelling loop without ever letting future
information leak backwards: per test window, every model is refit on
strictly earlier matches, rho is profiled on the same training window, and
bets are placed at pre-closing odds while CLV is measured against the
closing line only.
"""
from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pandas as pd

from xgedge.contracts import Col, Feat
from xgedge.decision.staking import demargin_shin, ev, kelly_stake, simulate_bankroll
from xgedge.evaluation.clv import clv_per_bet, summarize_clv
from xgedge.evaluation.metrics import (
    brier_1x2,
    brier_binary,
    logloss_1x2,
    logloss_binary,
)
from xgedge.evaluation.walkforward import walk_forward_splits
from xgedge.features.builder import build_features
from xgedge.markets.markets import prob_over, probs_1x2
from xgedge.models.baselines import GoalsAvgPoisson
from xgedge.models.dixon_coles import DixonColesClassic, fit_rho, score_matrix
from xgedge.models.poisson_glm import PoissonGBMModel, PoissonGLMModel

DEFAULT_MODELS = ["glm_dc", "gbm_dc", "dc_classic", "goals_poisson", "uniform", "market"]
PRIMARY_MODEL = "glm_dc"

DEFAULT_FEATURE_PARAMS = {
    "half_life_days": 180.0,
    "red_card_weight": 0.5,
    "adjust_opponent": False,
    "use_npxg": False,
    "decay": True,
    "min_history": 5,
    "venue_blend": 0.3,
    "clamp": (0.5, 2.0),
}

_FEATURE_COLS = [Feat.ATT_H, Feat.DEF_H, Feat.ATT_A, Feat.DEF_A]
_META_COLS = [Col.MATCH_ID, Col.DATE, Col.SEASON, Col.HOME, Col.AWAY,
              Col.FTHG, Col.FTAG, Col.FTR]


def _fit_lambda_model(name: str, train: pd.DataFrame):
    if name == "glm_dc":
        return PoissonGLMModel().fit(train)
    if name == "gbm_dc":
        return PoissonGBMModel().fit(train)
    if name == "dc_classic":
        return DixonColesClassic().fit(train)
    if name == "goals_poisson":
        return GoalsAvgPoisson().fit(train)
    return None


def _shin_or_nan(odds: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(odds), dtype=float)
    if np.any(~np.isfinite(arr)) or np.any(arr <= 1.0):
        return np.full(arr.shape, np.nan)
    return demargin_shin(arr)


def run_walkforward_eval(
    matches: pd.DataFrame,
    feature_params: Optional[dict] = None,
    initial_train_end: str = "2023-07-01",
    step_days: int = 30,
    edge_threshold: float = 0.03,
    kelly_fraction: float = 0.25,
    kelly_cap: float = 0.02,
    max_goals: int = 10,
    models: Optional[list] = None,
) -> dict:
    """Run the full leak-free walk-forward evaluation on cleaned matches.

    Returns a dict with per-model 1X2 and totals metrics, the tidy
    predictions DataFrame, the bet log with CLV, bankroll simulations and
    the config echo. ``feature_params`` are passed to ``build_features``;
    the special key ``force_rho_zero`` disables the Dixon-Coles correction
    (hypothesis H9) instead.
    """
    feature_params = {
        **DEFAULT_FEATURE_PARAMS,
        **dict(feature_params or {}),
    }
    force_rho_zero = bool(feature_params.pop("force_rho_zero", False))
    models = list(models) if models is not None else list(DEFAULT_MODELS)

    feats = build_features(matches, **feature_params).reset_index(drop=True)
    valid = (
        feats[Feat.IS_VALID].astype(bool)
        & feats[_FEATURE_COLS].notna().all(axis=1)
    ).to_numpy()

    pred_frames = []
    for train_idx, test_idx in walk_forward_splits(
        feats[Col.DATE], initial_train_end, step_days=step_days
    ):
        train = feats.iloc[train_idx[valid[train_idx]]]
        test = feats.iloc[test_idx[valid[test_idx]]]
        if train.empty or test.empty:
            continue

        window = test[_META_COLS].copy()
        for name in models:
            ph = pdr = pa = pover = np.full(len(test), np.nan)
            if name == "uniform":
                ph = np.full(len(test), 1 / 3)
                pdr = np.full(len(test), 1 / 3)
                pa = np.full(len(test), 1 / 3)
                pover = np.full(len(test), 0.5)
            elif name == "market":
                probs = np.array(
                    [_shin_or_nan([r[Col.PSCH], r[Col.PSCD], r[Col.PSCA]])
                     for _, r in test.iterrows()]
                )
                ph, pdr, pa = probs[:, 0], probs[:, 1], probs[:, 2]
                pover = np.array(
                    [_shin_or_nan([r[Col.PC_O25], r[Col.PC_U25]])[0]
                     for _, r in test.iterrows()]
                )
            else:
                model = _fit_lambda_model(name, train)
                if force_rho_zero or name == "goals_poisson":
                    rho = 0.0
                else:
                    tr_lh, tr_la = model.predict_lambdas(train)
                    rho = fit_rho(
                        tr_lh, tr_la,
                        train[Col.FTHG].to_numpy(), train[Col.FTAG].to_numpy(),
                    )
                lh, la = model.predict_lambdas(test)
                ph, pdr, pa, pover = (np.empty(len(test)) for _ in range(4))
                for i, (h, a) in enumerate(zip(lh, la)):
                    m = score_matrix(float(h), float(a), rho, max_goals=max_goals)
                    ph[i], pdr[i], pa[i] = probs_1x2(m)
                    pover[i] = prob_over(m, 2.5)
            window[f"{name}_ph"] = ph
            window[f"{name}_pd"] = pdr
            window[f"{name}_pa"] = pa
            window[f"{name}_pover25"] = pover
        pred_frames.append(window)

    if not pred_frames:
        raise ValueError("walk-forward produced no test windows; check dates/params")
    pred = pd.concat(pred_frames, ignore_index=True)

    results = {
        "models_1x2": _metrics_1x2(pred, models),
        "totals": _metrics_totals(pred, models),
        "predictions": pred,
        "config": {
            "initial_train_end": str(initial_train_end),
            "step_days": step_days,
            "edge_threshold": edge_threshold,
            "kelly_fraction": kelly_fraction,
            "kelly_cap": kelly_cap,
            "max_goals": max_goals,
            "force_rho_zero": force_rho_zero,
            "models": ",".join(models),
            **{f"feature_{k}": v for k, v in feature_params.items()},
        },
    }

    if PRIMARY_MODEL in models:
        bets = _collect_bets(
            pred, feats, edge_threshold, kelly_fraction, kelly_cap
        )
        results["bets"] = bets
        if len(bets):
            results["bankroll"] = {
                "kelly": simulate_bankroll(
                    bets, "kelly", fraction=kelly_fraction, cap=kelly_cap
                ),
                "flat": simulate_bankroll(bets, "flat"),
            }
            clvs = bets["clv"].dropna().to_numpy()
            if len(clvs):
                have_clv = bets["clv"].notna()
                results["clv"] = summarize_clv(
                    bets.loc[have_clv, "clv"].to_numpy(),
                    groups=bets.loc[have_clv, "match_id"].to_numpy(),
                )
    return results


def _metrics_1x2(pred: pd.DataFrame, models: list) -> dict:
    prob_cols = {m: [f"{m}_ph", f"{m}_pd", f"{m}_pa"] for m in models}
    have = {m: pred[cols].notna().all(axis=1) for m, cols in prob_cols.items()}
    common = np.logical_and.reduce([mask.to_numpy() for mask in have.values()])
    out = {}
    for m in models:
        mask = have[m].to_numpy()
        entry = {"brier": np.nan, "logloss": np.nan, "n": int(mask.sum()),
                 "brier_common": np.nan, "logloss_common": np.nan,
                 "n_common": int(common.sum())}
        if mask.any():
            p = pred.loc[mask, prob_cols[m]].to_numpy()
            y = pred.loc[mask, Col.FTR].tolist()
            entry["brier"] = brier_1x2(p, y)
            entry["logloss"] = logloss_1x2(p, y)
        if common.any():
            p = pred.loc[common, prob_cols[m]].to_numpy()
            y = pred.loc[common, Col.FTR].tolist()
            entry["brier_common"] = brier_1x2(p, y)
            entry["logloss_common"] = logloss_1x2(p, y)
        out[m] = entry
    return out


def _metrics_totals(pred: pd.DataFrame, models: list) -> dict:
    y_over = ((pred[Col.FTHG] + pred[Col.FTAG]) > 2.5).to_numpy().astype(float)
    prob_cols = {m: f"{m}_pover25" for m in models}
    have = {m: pred[col].notna() for m, col in prob_cols.items()}
    common = np.logical_and.reduce([mask.to_numpy() for mask in have.values()])
    out = {}
    for m in models:
        col = prob_cols[m]
        mask = have[m].to_numpy()
        entry = {
            "brier": np.nan,
            "logloss": np.nan,
            "n": int(mask.sum()),
            "brier_common": np.nan,
            "logloss_common": np.nan,
            "n_common": int(common.sum()),
        }
        if mask.any():
            p = pred.loc[mask, col].to_numpy()
            entry["brier"] = brier_binary(p, y_over[mask])
            entry["logloss"] = logloss_binary(p, y_over[mask])
        if common.any():
            p = pred.loc[common, col].to_numpy()
            entry["brier_common"] = brier_binary(p, y_over[common])
            entry["logloss_common"] = logloss_binary(p, y_over[common])
        out[m] = entry
    return out


def _collect_bets(
    pred: pd.DataFrame,
    feats: pd.DataFrame,
    edge_threshold: float,
    kelly_fraction: float,
    kelly_cap: float,
) -> pd.DataFrame:
    """Bet log for the primary model: pre-closing prices, CLV vs closing.

    Per match and market, at most the single highest-EV selection is backed,
    and only when EV clears ``edge_threshold``.
    """
    odds_cols = [
        Col.B365H, Col.B365D, Col.B365A,
        Col.PSCH, Col.PSCD, Col.PSCA,
        Col.B365_O25, Col.B365_U25, Col.B365C_O25, Col.B365C_U25,
        Col.PC_O25, Col.PC_U25,
    ]
    merged = pred.merge(
        feats[[Col.MATCH_ID] + odds_cols], on=Col.MATCH_ID, how="left"
    )
    rows = []
    for r in merged.itertuples(index=False):
        row = r._asdict()
        p1x2 = {
            "H": row[f"{PRIMARY_MODEL}_ph"],
            "D": row[f"{PRIMARY_MODEL}_pd"],
            "A": row[f"{PRIMARY_MODEL}_pa"],
        }
        if not any(np.isnan(list(p1x2.values()))):
            close_fair = _shin_or_nan(
                [row[Col.PSCH], row[Col.PSCD], row[Col.PSCA]]
            )
            candidates = []
            for i, (sel, o_col) in enumerate(
                zip("HDA", [Col.B365H, Col.B365D, Col.B365A])
            ):
                o = row[o_col]
                if np.isfinite(o) and o > 1.0:
                    candidates.append((ev(p1x2[sel], o), sel, p1x2[sel], o, i))
            if candidates:
                best = max(candidates)
                if best[0] > edge_threshold:
                    _, sel, p, o, i = best
                    clv = (
                        float(clv_per_bet(np.array([o]), close_fair[[i]])[0])
                        if np.isfinite(close_fair[i]) else np.nan
                    )
                    rows.append({
                        "date": row[Col.DATE], "match_id": row[Col.MATCH_ID],
                        "market": "1x2", "selection": sel, "p_model": p,
                        "odds": o, "ev": best[0],
                        "stake": kelly_stake(p, o, kelly_fraction, kelly_cap),
                        "won": row[Col.FTR] == sel, "clv": clv,
                    })
        p_over = row[f"{PRIMARY_MODEL}_pover25"]
        if np.isfinite(p_over):
            close_fair = _shin_or_nan([row[Col.PC_O25], row[Col.PC_U25]])
            total = row[Col.FTHG] + row[Col.FTAG]
            candidates = []
            for i, (sel, p, o_col) in enumerate(
                [("over", p_over, Col.B365_O25), ("under", 1 - p_over, Col.B365_U25)]
            ):
                o = row[o_col]
                if np.isfinite(o) and o > 1.0:
                    candidates.append((ev(p, o), sel, p, o, i))
            if candidates:
                best = max(candidates)
                if best[0] > edge_threshold:
                    _, sel, p, o, i = best
                    clv = (
                        float(clv_per_bet(np.array([o]), close_fair[[i]])[0])
                        if np.isfinite(close_fair[i]) else np.nan
                    )
                    rows.append({
                        "date": row[Col.DATE], "match_id": row[Col.MATCH_ID],
                        "market": "ou25", "selection": sel, "p_model": p,
                        "odds": o, "ev": best[0],
                        "stake": kelly_stake(p, o, kelly_fraction, kelly_cap),
                        "won": (total > 2.5) if sel == "over" else (total < 2.5),
                        "clv": clv,
                    })
    columns = ["date", "match_id", "market", "selection", "p_model",
               "odds", "ev", "stake", "won", "clv"]
    return pd.DataFrame(rows, columns=columns)
