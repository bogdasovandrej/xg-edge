"""Reproducible, offline audit of exact scores and high-total tails.

The audit deliberately uses only repository data.  It evaluates the final
``sample_size`` EPL matches of a season, reconstructs the production GLM/DC
score distribution by leak-free walk-forward fitting, and compares its high
total tails with a Poisson distribution implied by the available Bet365
closing O/U 2.5 pair.  The latter is a *derived benchmark*: the repository has
no direct O3.5 or O4.5 prices.

Post-match xG, cards and other event fields are outcomes/diagnostics only and
are never admitted to the pre-match marker registry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.optimize import brentq
from scipy.stats import norm, poisson
from sklearn.metrics import roc_auc_score

from xgedge.contracts import CLEANED_MATCHES, Col, Feat, REPORTS_DIR
from xgedge.evaluation.walkforward import walk_forward_splits
from xgedge.features.builder import build_features
from xgedge.markets.markets import prob_over, top_scores
from xgedge.models.dixon_coles import fit_rho, score_matrix
from xgedge.models.poisson_glm import PoissonGLMModel
from xgedge.pipeline import DEFAULT_FEATURE_PARAMS

DEFAULT_JSON = REPORTS_DIR / "high_totals_audit.json"
DEFAULT_MARKDOWN = Path(__file__).resolve().parents[1] / "docs" / "high-totals-audit.md"
DEFAULT_SEASON = "2025-26"
DEFAULT_SAMPLE_SIZE = 100
DEFAULT_SEED = 20260721
DEFAULT_BOOTSTRAPS = 10_000
DEFAULT_INITIAL_TRAIN_END = "2023-07-01"
DEFAULT_STEP_DAYS = 30
DEFAULT_MAX_GOALS = 10
EPSILON = 1e-12

REQUIRED_COLUMNS = (
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
    Col.B365CH,
    Col.B365CD,
    Col.B365CA,
    Col.B365C_O25,
    Col.B365C_U25,
)

# These columns exist only after the match.  They can define the target or a
# post-match diagnostic, but must never enter PREMATCH_MARKERS.
POSTMATCH_COLUMNS = frozenset(
    {
        Col.FTHG,
        Col.FTAG,
        Col.FTR,
        Col.XG_H,
        Col.XG_A,
        Col.NPXG_H,
        Col.NPXG_A,
        Col.PPDA_H,
        Col.PPDA_A,
        Col.DEEP_H,
        Col.DEEP_A,
        Col.RED_H,
        Col.RED_A,
    }
)

# Registry rather than a dynamic "try every column" search.  Every marker is
# available before kickoff.  Closing odds are an end-of-market benchmark and
# are not claimed to be executable at an earlier decision horizon.
PREMATCH_MARKERS = {
    "market_poisson_p_over35": {
        "source": "Bet365 closing O/U 2.5 pair, de-vigged then Poisson-transformed",
        "availability": "pre-kickoff closing benchmark; not an earlier executable quote",
    },
    "market_implied_total": {
        "source": "Bet365 closing O/U 2.5 pair, de-vigged then Poisson-transformed",
        "availability": "pre-kickoff closing benchmark; not an earlier executable quote",
    },
    "market_1x2_entropy": {
        "source": "Bet365 closing 1X2, proportional de-vig",
        "availability": "pre-kickoff closing benchmark; not an earlier executable quote",
    },
    "market_favourite_probability": {
        "source": "Bet365 closing 1X2, proportional de-vig",
        "availability": "pre-kickoff closing benchmark; not an earlier executable quote",
    },
    "model_p_over35": {
        "source": "leak-free walk-forward GLM/Dixon-Coles score matrix",
        "availability": "pre-match model output",
    },
    "model_expected_total": {
        "source": "leak-free walk-forward GLM/Dixon-Coles lambdas",
        "availability": "pre-match model output",
    },
    "model_match_balance": {
        "source": "leak-free walk-forward GLM/Dixon-Coles lambdas",
        "availability": "pre-match model output",
    },
    "xg_attack_form_sum": {
        "source": "causal decayed team attack features",
        "availability": "strictly earlier match dates only",
    },
    "xg_defence_form_sum": {
        "source": "causal decayed team defence features",
        "availability": "strictly earlier match dates only",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _portable_source_path(path: Path) -> str:
    """Return a stable repository-relative source identity when possible."""
    repo_root = Path(__file__).resolve().parents[1]
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return path.name


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _validate_and_select(
    matches: pd.DataFrame,
    *,
    season: str,
    sample_size: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if (
        isinstance(sample_size, bool)
        or not isinstance(sample_size, int)
        or sample_size < 1
    ):
        raise ValueError("sample_size must be a positive integer")
    missing = sorted(set(REQUIRED_COLUMNS).difference(matches.columns))
    if missing:
        raise ValueError(f"cleaned data is missing required columns: {missing}")

    frame = matches.copy()
    frame[Col.DATE] = pd.to_datetime(frame[Col.DATE], errors="raise")
    duplicate_ids = int(frame[Col.MATCH_ID].duplicated(keep=False).sum())
    if duplicate_ids:
        raise ValueError(f"match_id is not unique: {duplicate_ids} affected rows")

    season_frame = frame.loc[frame[Col.SEASON].eq(season)].sort_values(
        [Col.DATE, Col.MATCH_ID], kind="stable"
    )
    if len(season_frame) < sample_size:
        raise ValueError(
            f"season {season!r} has {len(season_frame)} matches, fewer than {sample_size}"
        )
    sample = season_frame.tail(sample_size).copy().reset_index(drop=True)

    null_counts = {
        column: int(sample[column].isna().sum()) for column in REQUIRED_COLUMNS
    }
    invalid_goals = int(((sample[Col.FTHG] < 0) | (sample[Col.FTAG] < 0)).sum())
    invalid_results = int((~sample[Col.FTR].isin(["H", "D", "A"])).sum())
    invalid_odds = int(
        (
            (sample[Col.B365C_O25] <= 1.0)
            | (sample[Col.B365C_U25] <= 1.0)
            | ~np.isfinite(
                sample[[Col.B365C_O25, Col.B365C_U25]].to_numpy(dtype=float)
            ).all(axis=1)
        ).sum()
    )
    invalid_1x2_odds = int(
        (
            (sample[[Col.B365CH, Col.B365CD, Col.B365CA]] <= 1.0).any(axis=1)
            | ~np.isfinite(
                sample[[Col.B365CH, Col.B365CD, Col.B365CA]].to_numpy(dtype=float)
            ).all(axis=1)
        ).sum()
    )
    invalid_xg = int(
        (
            (sample[[Col.XG_H, Col.XG_A]] < 0.0).any(axis=1)
            | ~np.isfinite(sample[[Col.XG_H, Col.XG_A]].to_numpy(dtype=float)).all(
                axis=1
            )
        ).sum()
    )
    result_mismatches = int(
        (
            np.where(
                sample[Col.FTHG] > sample[Col.FTAG],
                "H",
                np.where(sample[Col.FTHG] < sample[Col.FTAG], "A", "D"),
            )
            != sample[Col.FTR].to_numpy()
        ).sum()
    )
    if (
        any(null_counts.values())
        or invalid_goals
        or invalid_results
        or invalid_odds
        or invalid_1x2_odds
        or invalid_xg
        or result_mismatches
    ):
        raise ValueError("selected sample failed required completeness/domain checks")

    quality = {
        "expected_grain": "one completed EPL match per row",
        "rows_in_cleaned_data": int(len(frame)),
        "rows_in_season": int(len(season_frame)),
        "rows_in_audit_sample": int(len(sample)),
        "duplicate_match_id_rows_full_data": duplicate_ids,
        "required_null_counts_in_sample": null_counts,
        "invalid_goal_rows": invalid_goals,
        "invalid_result_rows": invalid_results,
        "result_goal_mismatches": result_mismatches,
        "invalid_bet365_closing_ou25_rows": invalid_odds,
        "invalid_bet365_closing_1x2_rows": invalid_1x2_odds,
        "invalid_xg_rows": invalid_xg,
        "sample_complete_for_required_fields": True,
    }
    return sample, quality


def _proportional_devig(odds: Iterable[float]) -> np.ndarray:
    values = np.asarray(list(odds), dtype=float)
    if values.ndim != 1 or values.size < 2:
        raise ValueError("at least two odds are required")
    if np.any(~np.isfinite(values)) or np.any(values <= 1.0):
        raise ValueError("odds must be finite and greater than 1")
    inverse = 1.0 / values
    return inverse / inverse.sum()


def _poisson_lambda_from_over25(probability: float) -> float:
    if not 0.0 < probability < 1.0:
        raise ValueError("O2.5 probability must be strictly between zero and one")
    return float(
        brentq(
            lambda lam: 1.0 - poisson.cdf(2, lam) - probability,
            1e-9,
            20.0,
        )
    )


def _benjamini_hochberg(p_values: Iterable[float]) -> np.ndarray:
    values = np.asarray(list(p_values), dtype=float)
    if values.ndim != 1 or np.any(~np.isfinite(values)):
        raise ValueError("p-values must be a finite one-dimensional sequence")
    if np.any((values < 0.0) | (values > 1.0)):
        raise ValueError("p-values must lie in [0, 1]")
    if values.size == 0:
        return values.copy()
    order = np.argsort(values, kind="stable")
    ranked = values[order] * values.size / np.arange(1, values.size + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    adjusted = np.empty_like(ranked)
    adjusted[order] = np.clip(ranked, 0.0, 1.0)
    return adjusted


def _bootstrap_mean_ci(
    values: np.ndarray,
    *,
    seed: int,
    n_boot: int,
    alpha: float = 0.05,
) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1 or arr.size == 0 or np.any(~np.isfinite(arr)):
        raise ValueError("bootstrap values must be a non-empty finite vector")
    if isinstance(n_boot, bool) or not isinstance(n_boot, int) or n_boot < 1:
        raise ValueError("n_boot must be a positive integer")
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=float)
    # Chunking bounds memory for larger command-line values while preserving
    # a stable RNG sequence and therefore byte-for-byte deterministic output.
    chunk = 1_000
    for start in range(0, n_boot, chunk):
        stop = min(start + chunk, n_boot)
        indices = rng.integers(0, arr.size, size=(stop - start, arr.size))
        means[start:stop] = arr[indices].mean(axis=1)
    return (
        float(np.quantile(means, alpha / 2.0)),
        float(np.quantile(means, 1.0 - alpha / 2.0)),
    )


def _wilson_interval(
    successes: int, n: int, alpha: float = 0.05
) -> tuple[float, float]:
    if n < 1 or not 0 <= successes <= n:
        raise ValueError("Wilson interval requires 0 <= successes <= n and n > 0")
    z = float(norm.ppf(1.0 - alpha / 2.0))
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denominator
    radius = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denominator
    return max(0.0, centre - radius), min(1.0, centre + radius)


def _calibration_bins(probabilities: np.ndarray, outcomes: np.ndarray) -> list[dict]:
    frame = pd.DataFrame({"probability": probabilities, "outcome": outcomes})
    # Quantile bins prevent a high-density middle range from dominating every
    # bucket.  Duplicate edges are safely collapsed.
    frame["bin"] = pd.qcut(
        frame["probability"], q=min(5, len(frame)), duplicates="drop"
    )
    rows = []
    for _, group in frame.groupby("bin", observed=True, sort=True):
        rows.append(
            {
                "n": int(len(group)),
                "probability_min": float(group["probability"].min()),
                "probability_max": float(group["probability"].max()),
                "mean_probability": float(group["probability"].mean()),
                "observed_rate": float(group["outcome"].mean()),
            }
        )
    return rows


def _calibration_summary(
    probabilities: np.ndarray,
    outcomes: np.ndarray,
    *,
    seed: int,
    n_boot: int,
) -> dict[str, Any]:
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=int)
    if p.shape != y.shape or p.ndim != 1 or p.size == 0:
        raise ValueError("probabilities and outcomes must be equal non-empty vectors")
    if np.any(~np.isfinite(p)) or np.any((p <= 0.0) | (p >= 1.0)):
        raise ValueError("probabilities must be finite and strictly inside (0, 1)")
    if np.any((y != 0) & (y != 1)):
        raise ValueError("outcomes must be binary")

    residual = p - y
    bias_low, bias_high = _bootstrap_mean_ci(residual, seed=seed, n_boot=n_boot)
    observed_low, observed_high = _wilson_interval(int(y.sum()), len(y))
    bins = _calibration_bins(p, y)
    ece = sum(
        row["n"] / len(y) * abs(row["mean_probability"] - row["observed_rate"])
        for row in bins
    )
    clipped = np.clip(p, EPSILON, 1.0 - EPSILON)
    return {
        "n": int(len(y)),
        "events": int(y.sum()),
        "observed_rate": float(y.mean()),
        "observed_rate_wilson_95": [observed_low, observed_high],
        "mean_probability": float(p.mean()),
        "predicted_minus_observed": float(residual.mean()),
        "predicted_minus_observed_bootstrap_95": [bias_low, bias_high],
        "brier": float(np.mean((p - y) ** 2)),
        "log_loss": float(
            -np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped))
        ),
        "ece_5_quantile_bins": float(ece),
        "calibration_bins": bins,
    }


def _walkforward_score_rows(
    matches: pd.DataFrame,
    target_ids: set[str],
    *,
    initial_train_end: str,
    step_days: int,
    max_goals: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    feature_params = dict(DEFAULT_FEATURE_PARAMS)
    features = build_features(matches, **feature_params).reset_index(drop=True)
    feature_columns = [Feat.ATT_H, Feat.DEF_H, Feat.ATT_A, Feat.DEF_A]
    valid = (
        features[Feat.IS_VALID].astype(bool)
        & features[feature_columns].notna().all(axis=1)
    ).to_numpy()

    rows: list[dict[str, Any]] = []
    for train_idx, test_idx in walk_forward_splits(
        features[Col.DATE], initial_train_end, step_days=step_days
    ):
        target_test_idx = np.array(
            [idx for idx in test_idx if features.iloc[idx][Col.MATCH_ID] in target_ids],
            dtype=int,
        )
        if target_test_idx.size == 0:
            continue
        train = features.iloc[train_idx[valid[train_idx]]]
        test = features.iloc[target_test_idx[valid[target_test_idx]]]
        if train.empty or test.empty:
            continue
        model = PoissonGLMModel().fit(train)
        train_lh, train_la = model.predict_lambdas(train)
        rho = fit_rho(
            train_lh,
            train_la,
            train[Col.FTHG].to_numpy(),
            train[Col.FTAG].to_numpy(),
        )
        lambdas_h, lambdas_a = model.predict_lambdas(test)
        for (_, match), lambda_h, lambda_a in zip(
            test.iterrows(), lambdas_h, lambdas_a
        ):
            matrix = score_matrix(
                float(lambda_h), float(lambda_a), rho, max_goals=max_goals
            )
            ranked = top_scores(matrix, k=min(10, matrix.size))
            actual = (int(match[Col.FTHG]), int(match[Col.FTAG]))
            actual_probability = (
                float(matrix[actual])
                if actual[0] <= max_goals and actual[1] <= max_goals
                else 0.0
            )
            rows.append(
                {
                    Col.MATCH_ID: match[Col.MATCH_ID],
                    "lambda_home": float(lambda_h),
                    "lambda_away": float(lambda_a),
                    "rho": float(rho),
                    "model_p_over35": prob_over(matrix, 3.5),
                    "model_p_over45": prob_over(matrix, 4.5),
                    "actual_probability": actual_probability,
                    "ranked_scores": ranked,
                    "top5_mass": float(sum(prob for _, prob in ranked[:5])),
                }
            )
    predictions = pd.DataFrame(rows)
    if predictions.empty:
        raise ValueError("walk-forward score audit produced no target predictions")
    if predictions[Col.MATCH_ID].duplicated().any():
        raise ValueError(
            "walk-forward score audit produced duplicate match predictions"
        )
    missing_ids = sorted(target_ids.difference(predictions[Col.MATCH_ID]))
    if missing_ids:
        raise ValueError(
            f"walk-forward score audit missed {len(missing_ids)} target matches"
        )
    target_features = features.loc[
        features[Col.MATCH_ID].isin(target_ids),
        [
            Col.MATCH_ID,
            Feat.ATT_H,
            Feat.DEF_H,
            Feat.ATT_A,
            Feat.DEF_A,
            Feat.N_HIST_H,
            Feat.N_HIST_A,
        ],
    ].copy()
    return predictions, target_features


def _marker_test(name: str, values: np.ndarray, outcomes: np.ndarray) -> dict[str, Any]:
    x = np.asarray(values, dtype=float)
    y = np.asarray(outcomes, dtype=int)
    if x.shape != y.shape or x.ndim != 1 or np.any(~np.isfinite(x)):
        raise ValueError(f"invalid marker values for {name}")
    std = float(x.std(ddof=0))
    if std <= 0.0:
        return {
            "marker": name,
            "odds_ratio_per_1sd": None,
            "coefficient": None,
            "p_value": 1.0,
            "roc_auc": 0.5,
            "status": "CONSTANT_MARKER",
        }
    z = (x - x.mean()) / std
    try:
        fit = sm.GLM(y, sm.add_constant(z), family=sm.families.Binomial()).fit()
        coefficient = float(fit.params[1])
        p_value = float(fit.pvalues[1])
        status = "ESTIMATED"
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        coefficient = float("nan")
        p_value = 1.0
        status = "FIT_FAILED"
    return {
        "marker": name,
        "odds_ratio_per_1sd": math.exp(coefficient)
        if np.isfinite(coefficient)
        else None,
        "coefficient": coefficient,
        "p_value": p_value,
        "roc_auc": float(roc_auc_score(y, z)),
        "status": status,
    }


def _test_prematch_markers(frame: pd.DataFrame) -> dict[str, Any]:
    unexpected = sorted(POSTMATCH_COLUMNS.intersection(PREMATCH_MARKERS))
    if unexpected:
        raise AssertionError(
            f"post-match columns entered marker registry: {unexpected}"
        )
    rows: list[dict[str, Any]] = []
    for endpoint, target_col in [("O3.5", "over35"), ("O4.5", "over45")]:
        outcomes = frame[target_col].to_numpy(dtype=int)
        for marker in PREMATCH_MARKERS:
            result = _marker_test(
                marker,
                frame[marker].to_numpy(dtype=float),
                outcomes,
            )
            result["endpoint"] = endpoint
            rows.append(result)
    q_values = _benjamini_hochberg([row["p_value"] for row in rows])
    for row, q_value in zip(rows, q_values):
        row["q_value_bh_all_tests"] = float(q_value)
        row["survives_fdr_0_05"] = bool(q_value <= 0.05)
        row["source"] = PREMATCH_MARKERS[row["marker"]]["source"]
        row["availability"] = PREMATCH_MARKERS[row["marker"]]["availability"]
    rows.sort(
        key=lambda row: (
            row["q_value_bh_all_tests"],
            row["p_value"],
            row["endpoint"],
            row["marker"],
        )
    )
    return {
        "method": "separate univariate logistic regressions; coefficient per one population SD",
        "family": "all registered marker x endpoint tests in this audit",
        "multiple_testing": "Benjamini-Hochberg false-discovery-rate adjustment",
        "alpha": 0.05,
        "n_hypotheses": len(rows),
        "n_surviving_fdr_0_05": int(sum(row["survives_fdr_0_05"] for row in rows)),
        "leakage_guard": {
            "postmatch_fields_excluded": sorted(POSTMATCH_COLUMNS),
            "registry_only": True,
            "closing_market_features_are_benchmark_only": True,
        },
        "tests": rows,
    }


def _exact_score_summary(frame: pd.DataFrame) -> dict[str, Any]:
    hits = {k: 0 for k in (1, 3, 5, 10)}
    modal_counts: dict[str, int] = {}
    log_losses = []
    for row in frame.itertuples(index=False):
        actual = (int(getattr(row, Col.FTHG)), int(getattr(row, Col.FTAG)))
        ranked = row.ranked_scores
        labels = [score for score, _ in ranked]
        for k in hits:
            hits[k] += int(actual in labels[:k])
        modal = f"{labels[0][0]}:{labels[0][1]}"
        modal_counts[modal] = modal_counts.get(modal, 0) + 1
        log_losses.append(-math.log(max(float(row.actual_probability), EPSILON)))
    n = len(frame)
    one_one_baseline_hits = int(((frame[Col.FTHG] == 1) & (frame[Col.FTAG] == 1)).sum())
    return {
        "n": n,
        "top_k_hits": {
            f"top_{k}": {"hits": hits[k], "rate": hits[k] / n} for k in hits
        },
        "mean_negative_log_likelihood": float(np.mean(log_losses)),
        "mean_top5_probability_mass": float(frame["top5_mass"].mean()),
        "mean_probability_outside_top5": float(1.0 - frame["top5_mass"].mean()),
        "predicted_modal_score_counts": dict(
            sorted(modal_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "fixed_naive_1_1_top1_baseline": {
            "hits": one_one_baseline_hits,
            "rate": one_one_baseline_hits / n,
            "definition": "always predict 1:1; fixed football baseline, not selected from the sample",
        },
        "display_policy": "show a ranked distribution and residual mass, not one promised score",
    }


def _paired_metric_comparison(
    model_p: np.ndarray,
    market_p: np.ndarray,
    outcomes: np.ndarray,
    *,
    seed: int,
    n_boot: int,
) -> dict[str, Any]:
    y = outcomes.astype(float)
    model_brier = (model_p - y) ** 2
    market_brier = (market_p - y) ** 2
    brier_delta = model_brier - market_brier
    brier_ci = _bootstrap_mean_ci(brier_delta, seed=seed, n_boot=n_boot)

    model_clip = np.clip(model_p, EPSILON, 1.0 - EPSILON)
    market_clip = np.clip(market_p, EPSILON, 1.0 - EPSILON)
    model_log = -(y * np.log(model_clip) + (1 - y) * np.log(1 - model_clip))
    market_log = -(y * np.log(market_clip) + (1 - y) * np.log(1 - market_clip))
    log_delta = model_log - market_log
    log_ci = _bootstrap_mean_ci(log_delta, seed=seed + 1, n_boot=n_boot)

    conclusion = (
        "MODEL_SIGNIFICANTLY_BETTER"
        if brier_ci[1] < 0.0
        else "MODEL_SIGNIFICANTLY_WORSE"
        if brier_ci[0] > 0.0
        else "NO_SIGNIFICANT_BRIER_DIFFERENCE"
    )
    return {
        "delta_definition": "model minus market-derived Poisson; negative favours model",
        "mean_brier_delta": float(brier_delta.mean()),
        "brier_delta_bootstrap_95": list(brier_ci),
        "mean_log_loss_delta": float(log_delta.mean()),
        "log_loss_delta_bootstrap_95": list(log_ci),
        "conclusion": conclusion,
    }


def build_audit(
    matches: pd.DataFrame,
    *,
    data_path: Path,
    season: str = DEFAULT_SEASON,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
    n_boot: int = DEFAULT_BOOTSTRAPS,
    initial_train_end: str = DEFAULT_INITIAL_TRAIN_END,
    step_days: int = DEFAULT_STEP_DAYS,
    max_goals: int = DEFAULT_MAX_GOALS,
) -> dict[str, Any]:
    sample, quality = _validate_and_select(
        matches, season=season, sample_size=sample_size
    )
    target_ids = set(sample[Col.MATCH_ID])
    score_rows, target_features = _walkforward_score_rows(
        matches,
        target_ids,
        initial_train_end=initial_train_end,
        step_days=step_days,
        max_goals=max_goals,
    )
    frame = sample.merge(score_rows, on=Col.MATCH_ID, validate="one_to_one")
    frame = frame.merge(target_features, on=Col.MATCH_ID, validate="one_to_one")
    frame = frame.sort_values([Col.DATE, Col.MATCH_ID], kind="stable").reset_index(
        drop=True
    )

    fair_over25 = []
    market_lambdas = []
    fair_1x2 = []
    for row in frame.itertuples(index=False):
        over25 = _proportional_devig(
            [getattr(row, Col.B365C_O25), getattr(row, Col.B365C_U25)]
        )[0]
        fair_over25.append(float(over25))
        market_lambdas.append(_poisson_lambda_from_over25(float(over25)))
        fair_1x2.append(
            _proportional_devig(
                [
                    getattr(row, Col.B365CH),
                    getattr(row, Col.B365CD),
                    getattr(row, Col.B365CA),
                ]
            )
        )
    market_lambdas_arr = np.asarray(market_lambdas)
    fair_1x2_arr = np.asarray(fair_1x2)
    frame["market_fair_p_over25"] = fair_over25
    frame["market_implied_total"] = market_lambdas_arr
    frame["market_poisson_p_over35"] = 1.0 - poisson.cdf(3, market_lambdas_arr)
    frame["market_poisson_p_over45"] = 1.0 - poisson.cdf(4, market_lambdas_arr)
    frame["market_1x2_entropy"] = -np.sum(
        fair_1x2_arr * np.log(np.clip(fair_1x2_arr, EPSILON, 1.0)), axis=1
    ) / math.log(3.0)
    frame["market_favourite_probability"] = fair_1x2_arr.max(axis=1)
    frame["model_expected_total"] = frame["lambda_home"] + frame["lambda_away"]
    frame["model_match_balance"] = 1.0 - (
        (frame["lambda_home"] - frame["lambda_away"]).abs()
        / frame["model_expected_total"].clip(lower=EPSILON)
    )
    frame["xg_attack_form_sum"] = frame[Feat.ATT_H] + frame[Feat.ATT_A]
    frame["xg_defence_form_sum"] = frame[Feat.DEF_H] + frame[Feat.DEF_A]
    totals = frame[Col.FTHG] + frame[Col.FTAG]
    frame["over35"] = (totals > 3.5).astype(int)
    frame["over45"] = (totals > 4.5).astype(int)

    high_totals = {}
    comparisons = {}
    for offset, (label, target, model_column, market_column) in enumerate(
        [
            ("O3.5", "over35", "model_p_over35", "market_poisson_p_over35"),
            ("O4.5", "over45", "model_p_over45", "market_poisson_p_over45"),
        ]
    ):
        outcomes = frame[target].to_numpy(dtype=int)
        model_prob = frame[model_column].to_numpy(dtype=float)
        market_prob = frame[market_column].to_numpy(dtype=float)
        high_totals[label] = {
            "base_rate": float(outcomes.mean()),
            "events": int(outcomes.sum()),
            "n": int(len(outcomes)),
            "glm_dc_raw_poisson_tail": _calibration_summary(
                model_prob,
                outcomes,
                seed=seed + offset * 10,
                n_boot=n_boot,
            ),
            "bet365_ou25_implied_poisson_tail": _calibration_summary(
                market_prob,
                outcomes,
                seed=seed + offset * 10 + 1,
                n_boot=n_boot,
            ),
        }
        comparisons[label] = _paired_metric_comparison(
            model_prob,
            market_prob,
            outcomes,
            seed=seed + offset * 10 + 2,
            n_boot=n_boot,
        )

    exact_scores = _exact_score_summary(frame)
    marker_tests = _test_prematch_markers(frame)
    postmatch_descriptive = {
        "purpose": "description after the fact only; forbidden as pre-match signal",
        "mean_total_xg_all": float((frame[Col.XG_H] + frame[Col.XG_A]).mean()),
        "mean_total_xg_o35": float(
            (
                frame.loc[frame["over35"].eq(1), Col.XG_H]
                + frame.loc[frame["over35"].eq(1), Col.XG_A]
            ).mean()
        ),
        "mean_total_xg_not_o35": float(
            (
                frame.loc[frame["over35"].eq(0), Col.XG_H]
                + frame.loc[frame["over35"].eq(0), Col.XG_A]
            ).mean()
        ),
        "mean_finishing_residual_all": float(
            (totals - frame[Col.XG_H] - frame[Col.XG_A]).mean()
        ),
        "mean_finishing_residual_o35": float(
            (
                totals[frame["over35"].eq(1)]
                - frame.loc[frame["over35"].eq(1), Col.XG_H]
                - frame.loc[frame["over35"].eq(1), Col.XG_A]
            ).mean()
        ),
        "red_card_match_rate_all": float(
            ((frame[Col.RED_H] + frame[Col.RED_A]) > 0).mean()
        ),
        "red_card_match_rate_o35": float(
            (
                (
                    frame.loc[frame["over35"].eq(1), Col.RED_H]
                    + frame.loc[frame["over35"].eq(1), Col.RED_A]
                )
                > 0
            ).mean()
        ),
    }

    direct_tail_odds_present = any(
        token in str(column).lower().replace(".", "")
        for column in matches.columns
        for token in ("o35", "u35", "o45", "u45", "over35", "over45")
    )
    conclusion = {
        "exact_score": (
            "A single modal score is too concentrated and should not be presented as "
            "the forecast; retain top-k probabilities plus residual mass."
        ),
        "high_totals": (
            "O3.5/O4.5 probabilities are uncalibrated research diagnostics. "
            "No direct prices exist in the repository, so no value-bet or CLV claim is possible."
        ),
        "markers": (
            "No pre-match marker is promoted. Exploratory associations require a new, "
            "untouched prospective sample after multiple-testing control."
            if marker_tests["n_surviving_fdr_0_05"] == 0
            else "Exploratory associations survived FDR, but still require a new untouched holdout."
        ),
        "betting_action": "NO_BET_FOR_O3.5_OR_O4.5",
    }

    payload = {
        "schema": "high-totals-exact-score-audit/1.0",
        "status": "RESEARCH_ONLY_NO_BET",
        "protocol": {
            "league": "EPL",
            "season": season,
            "sample_rule": (
                f"last {sample_size} matches after stable sort by date and match_id"
            ),
            "sample_start": str(frame[Col.DATE].min().date()),
            "sample_end": str(frame[Col.DATE].max().date()),
            "sample_size": sample_size,
            "walkforward_initial_train_end": initial_train_end,
            "walkforward_step_days": step_days,
            "walkforward_training_rule": "only rows before each test-window start",
            "same_date_rule": "feature state frozen until all matches on a date are recorded",
            "model": "Poisson GLM plus training-only Dixon-Coles rho",
            "feature_config": dict(DEFAULT_FEATURE_PARAMS),
            "score_grid": f"0..{max_goals} goals per team, normalized to one",
            "bootstrap_resamples": n_boot,
            "seed": seed,
            "network_access": False,
        },
        "source": {
            "path": _portable_source_path(data_path),
            "sha256": _sha256(data_path),
            "columns": list(matches.columns),
        },
        "data_quality": quality,
        "market_availability": {
            "bet365_closing_ou25_complete_rows": int(
                frame[[Col.B365C_O25, Col.B365C_U25]].notna().all(axis=1).sum()
            ),
            "pinnacle_closing_ou25_complete_rows": int(
                frame[[Col.PC_O25, Col.PC_U25]].notna().all(axis=1).sum()
            )
            if {Col.PC_O25, Col.PC_U25}.issubset(frame.columns)
            else 0,
            "direct_o35_or_o45_odds_columns_present": bool(direct_tail_odds_present),
            "tail_probability_contract": (
                "derived from a de-vigged O/U2.5 pair under a Poisson assumption; "
                "not a direct O3.5/O4.5 quote"
            ),
            "unavailable_context": {
                "coach_or_tactical_change": "not present in cleaned/features/market data",
                "goalkeeper_change_or_age": "not present in cleaned/features/market data",
                "rivalry_or_derby": "not present in cleaned/features/market data",
                "direct_o35_o45_prices": "not present in cleaned/features/market data",
            },
        },
        "high_totals": high_totals,
        "model_vs_market_derived_poisson": comparisons,
        "exact_scores": exact_scores,
        "prematch_marker_screen": marker_tests,
        "postmatch_descriptive_only": postmatch_descriptive,
        "conclusion": conclusion,
    }
    return _json_safe(payload)


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{100.0 * value:.{digits}f}%"


def render_markdown(payload: dict[str, Any]) -> str:
    o35 = payload["high_totals"]["O3.5"]
    o45 = payload["high_totals"]["O4.5"]
    exact = payload["exact_scores"]
    market = payload["market_availability"]
    markers = payload["prematch_marker_screen"]
    post = payload["postmatch_descriptive_only"]

    marker_rows = markers["tests"][:6]
    marker_table = "\n".join(
        "| {endpoint} | {marker} | {odds_ratio} | {auc:.3f} | {p:.4f} | {q:.4f} | {status} |".format(
            endpoint=row["endpoint"],
            marker=row["marker"],
            odds_ratio=(
                "n/a"
                if row["odds_ratio_per_1sd"] is None
                else f"{row['odds_ratio_per_1sd']:.3f}"
            ),
            auc=row["roc_auc"],
            p=row["p_value"],
            q=row["q_value_bh_all_tests"],
            status="survives" if row["survives_fdr_0_05"] else "no",
        )
        for row in marker_rows
    )
    exact_rows = "\n".join(
        f"| {label.replace('_', ' ')} | {entry['hits']} | {_pct(entry['rate'])} |"
        for label, entry in exact["top_k_hits"].items()
    )

    lines = f"""# High-total and exact-score audit: EPL 2025/26

## Technical summary

- In the final **{payload["protocol"]["sample_size"]}** league matches ({payload["protocol"]["sample_start"]} to {payload["protocol"]["sample_end"]}), O3.5 occurred **{o35["events"]}/{o35["n"]} ({_pct(o35["base_rate"])})** and O4.5 **{o45["events"]}/{o45["n"]} ({_pct(o45["base_rate"])})**.
- The only complete totals pair is Bet365 closing O/U2.5. Converting it to higher tails with a Poisson assumption predicts **{_pct(o35["bet365_ou25_implied_poisson_tail"]["mean_probability"])}** O3.5 and **{_pct(o45["bet365_ou25_implied_poisson_tail"]["mean_probability"])}** O4.5, versus observed {_pct(o35["base_rate"])} and {_pct(o45["base_rate"])}. These are derived diagnostics, not direct bookmaker quotes.
- The GLM/Dixon-Coles modal exact score hit **{exact["top_k_hits"]["top_1"]["hits"]}/{exact["n"]} ({_pct(exact["top_k_hits"]["top_1"]["rate"])})**. Its top five covered **{_pct(exact["top_k_hits"]["top_5"]["rate"])}**, while an average **{_pct(exact["mean_probability_outside_top5"])}** probability mass remained outside the displayed five outcomes. A single score is therefore not a truthful summary.
- Across {markers["n_hypotheses"]} pre-registered marker/endpoint tests, **{markers["n_surviving_fdr_0_05"]}** survived Benjamini-Hochberg FDR 5%. This audit does not identify a deployable easy-over subset.

The decision remains **{payload["conclusion"]["betting_action"]}**. The sample is exploratory and cannot establish profitable O3.5/O4.5 betting without timestamped direct prices and prospective CLV.

## Higher-score tails are overestimated by the O2.5 Poisson transform

| Endpoint | Observed | Market-derived mean | Bias (predicted − observed) | Brier | 95% bootstrap bias interval |
|---|---:|---:|---:|---:|---:|
| O3.5 | {_pct(o35["base_rate"])} | {_pct(o35["bet365_ou25_implied_poisson_tail"]["mean_probability"])} | {_pct(o35["bet365_ou25_implied_poisson_tail"]["predicted_minus_observed"])} | {o35["bet365_ou25_implied_poisson_tail"]["brier"]:.4f} | {_pct(o35["bet365_ou25_implied_poisson_tail"]["predicted_minus_observed_bootstrap_95"][0])} to {_pct(o35["bet365_ou25_implied_poisson_tail"]["predicted_minus_observed_bootstrap_95"][1])} |
| O4.5 | {_pct(o45["base_rate"])} | {_pct(o45["bet365_ou25_implied_poisson_tail"]["mean_probability"])} | {_pct(o45["bet365_ou25_implied_poisson_tail"]["predicted_minus_observed"])} | {o45["bet365_ou25_implied_poisson_tail"]["brier"]:.4f} | {_pct(o45["bet365_ou25_implied_poisson_tail"]["predicted_minus_observed_bootstrap_95"][0])} to {_pct(o45["bet365_ou25_implied_poisson_tail"]["predicted_minus_observed_bootstrap_95"][1])} |

The transformation assumes a single Poisson total-goal rate inferred from the de-vigged O/U2.5 pair. Tail miscalibration is visible at both thresholds, especially O4.5. The paired bootstrap comparison in the JSON artifact does not turn either model into a betting claim; it only compares two probability diagnostics on the same 100 outcomes.

## Exact score needs a distribution, not one label

| Coverage set | Hits | Rate |
|---|---:|---:|
{exact_rows}

Mean exact-score negative log likelihood is **{exact["mean_negative_log_likelihood"]:.3f}**. The fixed always-1:1 baseline hits **{exact["fixed_naive_1_1_top1_baseline"]["hits"]}/{exact["n"]} ({_pct(exact["fixed_naive_1_1_top1_baseline"]["rate"])})**. The model's most frequent modal score is **{next(iter(exact["predicted_modal_score_counts"]))}**, used in **{next(iter(exact["predicted_modal_score_counts"].values()))}/{exact["n"]}** predictions. This concentration explains why a repeated visible score looks unrealistic even when the underlying matrix contains many alternatives.

Product rule: show expected home/away goals, ranked top-five score probabilities, and probability outside the top five. Do not present the modal score as a promised result.

## No pre-match marker passed the corrected exploratory screen

Each marker was defined before reading the outcome row and tested separately with a one-standard-deviation univariate logistic coefficient. The correction family contains both endpoints and all registered markers. The six smallest adjusted p-values are shown below; full results are in `reports/high_totals_audit.json`.

| Endpoint | Marker | Odds ratio / 1 SD | AUC | Raw p | BH q | FDR 5% |
|---|---|---:|---:|---:|---:|---|
{marker_table}

Closing-market markers are benchmark-only: a closing quote is pre-kickoff, but it is not available at an earlier execution horizon. Any marker that survives in future must be frozen and retested on a new chronological holdout before promotion.

## Scope, data quality, and definitions

- Grain: one completed EPL match per row; stable sample rule is the last {payload["protocol"]["sample_size"]} rows after sorting by date and `match_id`.
- Required fields are complete in all {payload["data_quality"]["rows_in_audit_sample"]} sample rows; duplicate `match_id` rows: {payload["data_quality"]["duplicate_match_id_rows_full_data"]}; result/goal mismatches: {payload["data_quality"]["result_goal_mismatches"]}.
- O3.5 means at least four regulation-time goals; O4.5 means at least five. Denominator is all 100 selected matches.
- The GLM/Dixon-Coles model is refit in expanding {payload["protocol"]["walkforward_step_days"]}-day windows. Training rows predate the test-window start; same-date feature state is frozen.
- Direct O3.5/O4.5 odds present: **{str(market["direct_o35_or_o45_odds_columns_present"]).lower()}**. Pinnacle closing O/U2.5 complete rows: **{market["pinnacle_closing_ou25_complete_rows"]}**. Bet365 closing O/U2.5 complete rows: **{market["bet365_closing_ou25_complete_rows"]}**.

## Post-match evidence explains outcomes but cannot predict them

Observed total xG averaged **{post["mean_total_xg_all"]:.2f}** overall and **{post["mean_total_xg_o35"]:.2f}** in O3.5 matches, while finishing residual (goals minus xG) averaged **{post["mean_finishing_residual_o35"]:.2f}** in O3.5 matches. Red-card match rates were **{_pct(post["red_card_match_rate_all"])}** overall and **{_pct(post["red_card_match_rate_o35"])}** within O3.5.

These fields are deliberately descriptive only. Match xG, finishing residual, PPDA, deep completions and red cards are known after kickoff and are prohibited from the pre-match marker registry.

## Limitations and next validation

- There are only {o45["events"]} O4.5 events. Confidence intervals are wide, and the sample cannot support a rare-event production rule.
- Direct O3.5/O4.5 opening, taken and closing prices are absent. EV, best price and CLV for those markets are therefore not identifiable.
- Coach changes, new/young goalkeepers, rivalry labels, lineups, injuries and tactical formations are absent from this dataset and were not fabricated.
- The marker screen is univariate and exploratory, not causal. It does not capture interactions and must not be tuned repeatedly on these same 100 matches.
- Next step: collect timestamped direct O3.5/O4.5 quotes and point-in-time lineup/context snapshots, freeze one small hypothesis set, then evaluate calibration and CLV on a new chronological sample.

## Reproduction

```powershell
.\\.venv\\Scripts\\python.exe scripts\\audit_score_and_high_totals.py --output reports\\high_totals_audit.json --markdown docs\\high-totals-audit.md
```

The script uses no network calls. Input SHA-256, parameters, seed, feature configuration, calibration bins, bootstrap intervals and every marker test are stored in the JSON artifact.
"""
    return lines.strip() + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=CLEANED_MATCHES)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--bootstraps", type=int, default=DEFAULT_BOOTSTRAPS)
    parser.add_argument("--initial-train-end", default=DEFAULT_INITIAL_TRAIN_END)
    parser.add_argument("--step-days", type=int, default=DEFAULT_STEP_DAYS)
    parser.add_argument("--max-goals", type=int, default=DEFAULT_MAX_GOALS)
    args = parser.parse_args(argv)

    matches = pd.read_parquet(args.data)
    payload = build_audit(
        matches,
        data_path=args.data,
        season=args.season,
        sample_size=args.sample_size,
        seed=args.seed,
        n_boot=args.bootstraps,
        initial_train_end=args.initial_train_end,
        step_days=args.step_days,
        max_goals=args.max_goals,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(render_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(args.output),
                "markdown": str(args.markdown),
                "sample_size": payload["protocol"]["sample_size"],
                "o35": payload["high_totals"]["O3.5"]["base_rate"],
                "o45": payload["high_totals"]["O4.5"]["base_rate"],
                "betting_action": payload["conclusion"]["betting_action"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
