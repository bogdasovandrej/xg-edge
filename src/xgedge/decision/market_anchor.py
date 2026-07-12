"""Market-aware probability anchoring and a confidence-based CLV gate.

The anchor treats de-vigged opening prices as the prior.  A football model is
allowed to move that prior only through its centered-log-ratio (CLR) residual,
and that residual is shrunk more aggressively in the longshot bucket.  Model
selection is deliberately split into an early development fit and a later
development selection period; this module has no holdout input in its
selection API.

The betting guard is intentionally separate from probability calibration.
Positive point expected value is never sufficient: the lower endpoint of a
match-cluster bootstrap confidence interval for historical CLV must be above
zero and the history must contain enough independent matches.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from xgedge.evaluation.clv import summarize_clv

_EPS = 1e-12


def _probability_matrix(values: np.ndarray, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"{name} must have shape (n, 3)")
    if not np.isfinite(arr).all() or np.any(arr <= 0.0):
        raise ValueError(f"{name} must contain finite positive values")
    totals = arr.sum(axis=1, keepdims=True)
    if np.any(totals <= 0.0):
        raise ValueError(f"{name} rows must have positive sums")
    return arr / totals


def devig_opening_odds(odds: np.ndarray) -> np.ndarray:
    """Return proportional de-vigged probabilities for 1X2 opening odds."""
    arr = np.asarray(odds, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError("odds must have shape (n, 3)")
    if not np.isfinite(arr).all() or np.any(arr <= 1.0):
        raise ValueError("odds must contain finite decimal prices above 1")
    implied = 1.0 / arr
    return implied / implied.sum(axis=1, keepdims=True)


def centered_log_ratio(probs: np.ndarray) -> np.ndarray:
    """Map three-part compositions to centered log-ratio coordinates."""
    p = _probability_matrix(probs, "probs")
    logp = np.log(np.clip(p, _EPS, 1.0))
    return logp - logp.mean(axis=1, keepdims=True)


def _softmax(logits: np.ndarray) -> np.ndarray:
    z = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(z)
    return exp / exp.sum(axis=1, keepdims=True)


def _outcome_indices(outcomes: Sequence[str] | np.ndarray) -> np.ndarray:
    arr = np.asarray(outcomes)
    mapping = {"H": 0, "D": 1, "A": 2}
    try:
        result = np.array([mapping[str(value)] for value in arr], dtype=int)
    except KeyError as exc:
        raise ValueError("outcomes must contain only H, D or A") from exc
    return result


@dataclass(frozen=True)
class AnchorConfig:
    """A development-selected market-anchor and candidate policy."""

    residual_weight: float = 0.25
    longshot_weight: float = 0.0
    longshot_probability: float = 0.15
    edge_threshold: float = 0.05
    max_odds: float = 8.0
    bias_l2: float = 0.10

    def __post_init__(self) -> None:
        if not 0.0 <= self.residual_weight <= 1.0:
            raise ValueError("residual_weight must be in [0, 1]")
        if not 0.0 <= self.longshot_weight <= 1.0:
            raise ValueError("longshot_weight must be in [0, 1]")
        if not 0.0 < self.longshot_probability < 1.0 / 3.0:
            raise ValueError("longshot_probability must be in (0, 1/3)")
        if self.edge_threshold < 0.0:
            raise ValueError("edge_threshold must be non-negative")
        if self.max_odds <= 1.0:
            raise ValueError("max_odds must be above 1")
        if self.bias_l2 < 0.0:
            raise ValueError("bias_l2 must be non-negative")


class MarketAnchor:
    """Opening-market prior plus a shrunken football-model CLR residual."""

    def __init__(self, config: AnchorConfig, bias: np.ndarray | None = None):
        self.config = config
        if bias is None:
            self.bias_ = np.zeros(3, dtype=float)
            self.is_fitted_ = False
        else:
            arr = np.asarray(bias, dtype=float)
            if arr.shape != (3,) or not np.isfinite(arr).all():
                raise ValueError("bias must contain three finite values")
            self.bias_ = arr - arr.mean()
            self.is_fitted_ = True

    def _residual(self, raw_probs: np.ndarray, market_probs: np.ndarray) -> np.ndarray:
        raw = _probability_matrix(raw_probs, "raw_probs")
        market = _probability_matrix(market_probs, "market_probs")
        if len(raw) != len(market):
            raise ValueError("raw_probs and market_probs must have equal length")
        residual = centered_log_ratio(raw) - centered_log_ratio(market)
        bucket_weight = np.where(
            market < self.config.longshot_probability,
            self.config.longshot_weight,
            1.0,
        )
        adjusted = self.config.residual_weight * bucket_weight * residual
        return adjusted - adjusted.mean(axis=1, keepdims=True)

    def _predict_from_market(
        self,
        raw_probs: np.ndarray,
        market_probs: np.ndarray,
        bias: np.ndarray,
    ) -> np.ndarray:
        market = _probability_matrix(market_probs, "market_probs")
        residual = self._residual(raw_probs, market)
        return _softmax(np.log(market) + residual + bias)

    def fit(
        self,
        raw_probs: np.ndarray,
        opening_odds: np.ndarray,
        outcomes: Sequence[str] | np.ndarray,
    ) -> "MarketAnchor":
        """Fit only a centered intercept; residual weights remain pre-specified."""
        market = devig_opening_odds(opening_odds)
        raw = _probability_matrix(raw_probs, "raw_probs")
        y = _outcome_indices(outcomes)
        if len(raw) != len(market) or len(y) != len(raw):
            raise ValueError("fit inputs must have equal length")
        if len(y) == 0:
            raise ValueError("cannot fit an empty sample")

        def unpack(two_biases: np.ndarray) -> np.ndarray:
            return np.array(
                [two_biases[0], two_biases[1], -two_biases.sum()], dtype=float
            )

        def objective(two_biases: np.ndarray) -> float:
            bias = unpack(two_biases)
            p = self._predict_from_market(raw, market, bias)
            nll = -np.log(np.clip(p[np.arange(len(y)), y], _EPS, 1.0)).mean()
            return float(nll + self.config.bias_l2 * np.square(bias).sum())

        fitted = minimize(objective, np.zeros(2), method="BFGS")
        if not fitted.success or not np.isfinite(fitted.fun):
            raise RuntimeError(f"market-anchor calibration failed: {fitted.message}")
        self.bias_ = unpack(np.asarray(fitted.x, dtype=float))
        self.is_fitted_ = True
        return self

    def predict_proba(
        self, raw_probs: np.ndarray, opening_odds: np.ndarray
    ) -> np.ndarray:
        if not self.is_fitted_:
            raise RuntimeError("MarketAnchor must be fitted before prediction")
        market = devig_opening_odds(opening_odds)
        return self._predict_from_market(raw_probs, market, self.bias_)

    def to_dict(self) -> dict:
        return {
            "config": asdict(self.config),
            "bias": [float(x) for x in self.bias_],
        }


def probability_metrics(probs: np.ndarray, outcomes: Sequence[str]) -> dict:
    p = _probability_matrix(probs, "probs")
    y = _outcome_indices(outcomes)
    if len(y) != len(p):
        raise ValueError("probs and outcomes must have equal length")
    onehot = np.eye(3)[y]
    return {
        "n": int(len(y)),
        "logloss": float(
            -np.log(np.clip(p[np.arange(len(y)), y], _EPS, 1.0)).mean()
        ),
        "brier": float(np.square(p - onehot).sum(axis=1).mean()),
    }


def candidate_bets_1x2(
    probs: np.ndarray,
    taken_odds: np.ndarray,
    closing_odds: np.ndarray,
    match_ids: Sequence[object],
    *,
    edge_threshold: float,
    max_odds: float,
) -> pd.DataFrame:
    """Select at most one pre-match 1X2 candidate per independent match."""
    p = _probability_matrix(probs, "probs")
    taken = np.asarray(taken_odds, dtype=float)
    close = np.asarray(closing_odds, dtype=float)
    ids = np.asarray(match_ids)
    if taken.shape != p.shape or close.shape != p.shape or len(ids) != len(p):
        raise ValueError("probabilities, odds and match_ids must have equal lengths")

    rows: list[dict] = []
    labels = np.array(["H", "D", "A"])
    for i in range(len(p)):
        if (
            not np.isfinite(taken[i]).all()
            or not np.isfinite(close[i]).all()
            or np.any(taken[i] <= 1.0)
            or np.any(close[i] <= 1.0)
        ):
            continue
        value = p[i] * taken[i] - 1.0
        allowed = taken[i] <= max_odds
        if not allowed.any():
            continue
        masked = np.where(allowed, value, -np.inf)
        selection = int(np.argmax(masked))
        if masked[selection] <= edge_threshold:
            continue
        fair_close = devig_opening_odds(close[i : i + 1])[0]
        rows.append(
            {
                "match_id": ids[i],
                "selection": str(labels[selection]),
                "p_model": float(p[i, selection]),
                "odds": float(taken[i, selection]),
                "point_ev": float(masked[selection]),
                "clv": float(taken[i, selection] * fair_close[selection] - 1.0),
            }
        )
    return pd.DataFrame(
        rows,
        columns=["match_id", "selection", "p_model", "odds", "point_ev", "clv"],
    )


def clv_betting_gate(
    historical_clv: Iterable[float],
    match_ids: Iterable[object],
    *,
    min_independent_matches: int = 100,
    n_boot: int = 10_000,
    seed: int = 0,
) -> dict:
    """Return BET only with enough clusters and a strictly positive lower CI."""
    if (
        isinstance(min_independent_matches, bool)
        or not isinstance(min_independent_matches, (int, np.integer))
        or min_independent_matches <= 0
    ):
        raise ValueError("min_independent_matches must be a positive integer")
    clv = np.asarray(list(historical_clv), dtype=float)
    groups = np.asarray(list(match_ids))
    if clv.ndim != 1 or groups.ndim != 1 or len(clv) != len(groups):
        raise ValueError("historical_clv and match_ids must be equal 1D arrays")
    finite = np.isfinite(clv)
    clv = clv[finite]
    groups = groups[finite]
    summary = summarize_clv(clv, groups=groups, n_boot=n_boot, seed=seed)
    enough = summary["n_clusters"] >= min_independent_matches
    positive = enough and np.isfinite(summary["ci_low"]) and summary["ci_low"] > 0.0
    if not enough:
        reason = "insufficient_independent_matches"
    elif not positive:
        reason = "clv_lower_ci_not_positive"
    else:
        reason = "positive_clv_confirmed"
    return {
        "action": "BET" if positive else "NO BET",
        "reason": reason,
        "min_independent_matches": int(min_independent_matches),
        "clv": summary,
    }


def guarded_bet_decision(
    probability: float,
    odds: float,
    historical_clv: Iterable[float],
    historical_match_ids: Iterable[object],
    *,
    edge_threshold: float = 0.05,
    min_independent_matches: int = 100,
    n_boot: int = 10_000,
    seed: int = 0,
) -> dict:
    """Apply the CLV gate after validating price and point EV."""
    if not np.isfinite(probability) or not 0.0 < probability < 1.0:
        raise ValueError("probability must be finite and in (0, 1)")
    if not np.isfinite(odds) or odds <= 1.0:
        raise ValueError("odds must be a finite decimal price above 1")
    point_ev = float(probability * odds - 1.0)
    if point_ev <= edge_threshold:
        return {
            "action": "NO BET",
            "reason": "point_ev_below_threshold",
            "point_ev": point_ev,
        }
    gate = clv_betting_gate(
        historical_clv,
        historical_match_ids,
        min_independent_matches=min_independent_matches,
        n_boot=n_boot,
        seed=seed,
    )
    return {**gate, "point_ev": point_ev}


def default_anchor_grid() -> list[AnchorConfig]:
    """Small, fixed grid whose members may be selected on development only."""
    return [
        AnchorConfig(residual, longshot, bucket, edge, max_odds)
        for residual in (0.0, 0.15, 0.30, 0.50)
        for longshot in (0.0, 0.25, 0.50)
        for bucket in (0.10, 0.15)
        for edge in (0.03, 0.05)
        for max_odds in (6.0, 10.0)
    ]


def select_anchor_on_late_development(
    *,
    early_raw_probs: np.ndarray,
    early_opening_odds: np.ndarray,
    early_outcomes: Sequence[str],
    late_raw_probs: np.ndarray,
    late_opening_odds: np.ndarray,
    late_taken_odds: np.ndarray,
    late_closing_odds: np.ndarray,
    late_outcomes: Sequence[str],
    late_match_ids: Sequence[object],
    configs: Sequence[AnchorConfig] | None = None,
    min_selection_matches: int = 50,
    n_boot: int = 2_000,
    seed: int = 0,
    max_market_logloss_regression: float = 0.005,
) -> tuple[MarketAnchor, list[dict]]:
    """Fit on early development, select once on later development.

    Configurations with enough candidate clusters are ranked by the lower CLV
    confidence limit, subject to a loose market-logloss safety constraint.
    When none has enough candidates, the lowest late-development logloss wins.
    The signature intentionally accepts no holdout observations.
    """
    grid = list(configs) if configs is not None else default_anchor_grid()
    if not grid:
        raise ValueError("configs must not be empty")
    market_late = devig_opening_odds(late_opening_odds)
    market_logloss = probability_metrics(market_late, late_outcomes)["logloss"]
    rows: list[dict] = []
    fitted_models: list[MarketAnchor] = []
    for index, config in enumerate(grid):
        model = MarketAnchor(config).fit(
            early_raw_probs, early_opening_odds, early_outcomes
        )
        probs = model.predict_proba(late_raw_probs, late_opening_odds)
        metrics = probability_metrics(probs, late_outcomes)
        candidates = candidate_bets_1x2(
            probs,
            late_taken_odds,
            late_closing_odds,
            late_match_ids,
            edge_threshold=config.edge_threshold,
            max_odds=config.max_odds,
        )
        gate = clv_betting_gate(
            candidates["clv"],
            candidates["match_id"],
            min_independent_matches=min_selection_matches,
            n_boot=n_boot,
            seed=seed,
        )
        safe_logloss = metrics["logloss"] <= (
            market_logloss + max_market_logloss_regression
        )
        eligible = (
            gate["clv"]["n_clusters"] >= min_selection_matches and safe_logloss
        )
        rows.append(
            {
                "grid_index": index,
                "config": asdict(config),
                "metrics": metrics,
                "market_logloss": market_logloss,
                "candidate_count": int(len(candidates)),
                "gate": gate,
                "selection_eligible": bool(eligible),
            }
        )
        fitted_models.append(model)

    eligible_indices = [
        i for i, row in enumerate(rows) if row["selection_eligible"]
    ]
    if eligible_indices:
        best = max(
            eligible_indices,
            key=lambda i: (
                rows[i]["gate"]["clv"]["ci_low"],
                -rows[i]["metrics"]["logloss"],
                -i,
            ),
        )
        selection_rule = "highest_late_development_clv_lower_ci"
    else:
        best = min(
            range(len(rows)),
            key=lambda i: (rows[i]["metrics"]["logloss"], i),
        )
        selection_rule = "fallback_lowest_late_development_logloss"
    for i, row in enumerate(rows):
        row["selected"] = i == best
        row["selection_rule"] = selection_rule if i == best else None
    return fitted_models[best], rows
