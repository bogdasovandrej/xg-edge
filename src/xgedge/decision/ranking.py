"""Strict, deterministic ranking for PAPER-only market candidates."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping


@dataclass(frozen=True)
class PaperRankingConfig:
    """Frozen v2 selector based on a one-sided 95% lower EV bound."""

    version: str = "paper-ranking-v2-lcb95"
    maximum_odds: float = 6.0
    minimum_data_quality: float = 60.0
    quality_target: float = 85.0
    maximum_quality_probability_error: float = 0.02
    maximum_candidates: int = 10
    z_score_one_sided_95: float = 1.6448536269514722
    probability_se_low: float = 0.01
    probability_se_medium: float = 0.02
    probability_se_high: float = 0.035
    probability_se_unknown: float = 0.05
    default_calibration_error: float = 0.015
    default_commission_rate: float = 0.0
    expected_slippage_rate: float = 0.005
    maximum_flat_stake_fraction: float = 0.0025

    def validate(self) -> None:
        numeric = (
            self.maximum_odds,
            self.minimum_data_quality,
            self.quality_target,
            self.maximum_quality_probability_error,
            self.z_score_one_sided_95,
            self.probability_se_low,
            self.probability_se_medium,
            self.probability_se_high,
            self.probability_se_unknown,
            self.default_calibration_error,
            self.default_commission_rate,
            self.expected_slippage_rate,
            self.maximum_flat_stake_fraction,
        )
        if not all(isfinite(float(value)) for value in numeric):
            raise ValueError("paper ranking config must be finite")
        if self.maximum_odds <= 1:
            raise ValueError("maximum_odds must be above 1")
        if not 0 <= self.minimum_data_quality <= self.quality_target <= 100:
            raise ValueError("data-quality thresholds must satisfy 0 <= minimum <= target <= 100")
        rates = (
            self.maximum_quality_probability_error,
            self.probability_se_low,
            self.probability_se_medium,
            self.probability_se_high,
            self.probability_se_unknown,
            self.default_calibration_error,
            self.default_commission_rate,
            self.expected_slippage_rate,
            self.maximum_flat_stake_fraction,
        )
        if any(not 0 <= value < 1 for value in rates) or self.z_score_one_sided_95 <= 0:
            raise ValueError("LCB errors, frictions and stake cap must be in valid ranges")
        if (
            isinstance(self.maximum_candidates, bool)
            or not isinstance(self.maximum_candidates, int)
            or self.maximum_candidates < 1
        ):
            raise ValueError("maximum_candidates must be a positive integer")


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _probability_standard_error(label: Any, config: PaperRankingConfig) -> float:
    return {
        "low": config.probability_se_low,
        "низкая": config.probability_se_low,
        "medium": config.probability_se_medium,
        "средняя": config.probability_se_medium,
        "high": config.probability_se_high,
        "высокая": config.probability_se_high,
    }.get(str(label or "").strip().casefold(), config.probability_se_unknown)


def _candidate_source(details: Mapping[str, Any]) -> tuple[list[Any], Mapping[str, Any], str]:
    live = details.get("market_candidates")
    expanded = details.get("expanded_market_candidates")
    snapshot = details.get("market_snapshot")
    if isinstance(snapshot, Mapping) and (
        isinstance(live, list) or isinstance(expanded, list)
    ):
        rows = [
            *list(live if isinstance(live, list) else []),
            *list(expanded if isinstance(expanded, list) else []),
        ]
        return rows, snapshot, "live_best_price"
    manual = details.get("candidate_bets")
    market = details.get("market")
    if isinstance(manual, list) and isinstance(market, Mapping):
        return manual, market, "audited_market_snapshot"
    return [], {}, "missing_market"


def rank_paper_candidates(
    payload: Mapping[str, Any],
    config: PaperRankingConfig | None = None,
) -> dict[str, Any]:
    """Rank at most one PAPER candidate per match and fail closed on weak data.

    This function does not claim that the model probability is the true
    probability.  It requires the one-sided 95% lower bound of execution EV to
    stay above zero after probability uncertainty, calibration error,
    data-quality error, commission and expected price slippage.
    """

    cfg = config or PaperRankingConfig()
    cfg.validate()
    forecasts = payload.get("forecasts")
    if not isinstance(forecasts, list):
        raise ValueError("live payload forecasts must be a list")
    generated_at = _utc(payload.get("generated_at"))
    if generated_at is None:
        raise ValueError("live payload generated_at must be a timezone-aware ISO timestamp")

    eligible: list[dict[str, Any]] = []
    rejection_counts: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    for forecast in forecasts:
        if not isinstance(forecast, Mapping) or not forecast.get("id"):
            reject("invalid_forecast")
            continue
        kickoff = _utc(forecast.get("kickoff_utc"))
        if kickoff is None or kickoff <= generated_at:
            reject("fixture_not_future")
            continue
        forecast_generated = _utc(
            forecast.get("forecast_generated_at") or payload.get("generated_at")
        )
        if forecast_generated is None or forecast_generated >= kickoff:
            reject("invalid_forecast_timestamp")
            continue
        details = forecast.get("details")
        if not isinstance(details, Mapping):
            reject("missing_dossier")
            continue
        candidates, market, source_kind = _candidate_source(details)
        if not candidates:
            reject("missing_verified_market_candidates")
            continue
        if source_kind == "live_best_price" and market.get("status") != "SHADOW_ONLY":
            reject("market_snapshot_not_eligible")
            continue
        captured_at = market.get("captured_at_utc")
        captured = _utc(captured_at)
        if captured is None:
            reject("invalid_quote_timestamp")
            continue
        if captured < forecast_generated or captured >= kickoff:
            reject("quote_outside_forecast_window")
            continue
        quality = _finite((details.get("data_quality") or {}).get("score"))
        if quality is None or quality < cfg.minimum_data_quality:
            reject("data_quality_below_threshold")
            continue
        quality_gap = max(0.0, cfg.quality_target - quality)
        quality_span = max(cfg.quality_target - cfg.minimum_data_quality, 1.0)
        quality_error = min(
            cfg.maximum_quality_probability_error,
            cfg.maximum_quality_probability_error * quality_gap / quality_span,
        )
        default_probability_se = _probability_standard_error(
            forecast.get("uncertainty"), cfg
        )
        match_rows: list[dict[str, Any]] = []
        for source in candidates:
            if not isinstance(source, Mapping):
                continue
            probability = _finite(source.get("probability"))
            odds = _finite(source.get("market_odds"))
            point_edge = _finite(source.get("point_edge"))
            calculated_point_edge = (
                probability * odds - 1.0
                if probability is not None and odds is not None
                else None
            )
            if (
                probability is None
                or odds is None
                or point_edge is None
                or not 0 < probability < 1
                or not 1 < odds <= cfg.maximum_odds
                or calculated_point_edge is None
                or abs(point_edge - calculated_point_edge) > 1e-8
            ):
                continue
            probability_se = (
                _finite(source.get("probability_std_error"))
                if source.get("probability_std_error") is not None
                else default_probability_se
            )
            calibration_error = (
                _finite(source.get("calibration_error"))
                if source.get("calibration_error") is not None
                else cfg.default_calibration_error
            )
            commission_rate = (
                _finite(source.get("commission_rate"))
                if source.get("commission_rate") is not None
                else cfg.default_commission_rate
            )
            slippage_rate = (
                _finite(source.get("expected_slippage_rate"))
                if source.get("expected_slippage_rate") is not None
                else cfg.expected_slippage_rate
            )
            if (
                probability_se is None
                or calibration_error is None
                or commission_rate is None
                or slippage_rate is None
                or not 0 <= probability_se < 1
                or not 0 <= calibration_error < 1
                or not 0 <= commission_rate < 1
                or not 0 <= slippage_rate < 1
            ):
                continue
            probability_lcb95 = max(
                0.0,
                probability
                - cfg.z_score_one_sided_95 * probability_se
                - calibration_error
                - quality_error,
            )
            executable_odds_after_friction = 1.0 + (
                (odds - 1.0) * (1.0 - commission_rate) * (1.0 - slippage_rate)
            )
            ev_lcb95 = probability_lcb95 * executable_odds_after_friction - 1.0
            if ev_lcb95 <= 0:
                continue
            bookmaker = source.get("bookmaker") or market.get("bookmaker")
            if not isinstance(bookmaker, str) or not bookmaker.strip():
                continue
            match_rows.append({
                "fixture_id": str(forecast["id"]),
                "competition": forecast.get("competition"),
                "stage": forecast.get("stage"),
                "kickoff_utc": forecast.get("kickoff_utc"),
                "home": forecast.get("home"),
                "away": forecast.get("away"),
                "selection": source.get("selection"),
                "outcome": source.get("outcome"),
                "market": source.get("market") or "1x2",
                "line": source.get("line"),
                "model_probability": probability,
                "break_even_probability": 1.0 / odds,
                "probability_edge": probability - 1.0 / odds,
                "odds": odds,
                "bookmaker": bookmaker,
                "bookmaker_key": source.get("bookmaker_key"),
                "quote_source": source.get("source_provider") or market.get("source_provider"),
                "quote_captured_at": captured_at,
                "point_edge": calculated_point_edge,
                "robust_edge": ev_lcb95,
                "ev_lcb95": ev_lcb95,
                "probability_lcb95": probability_lcb95,
                "executable_odds_after_friction": executable_odds_after_friction,
                "lcb95_inputs": {
                    "z_score": cfg.z_score_one_sided_95,
                    "probability_std_error": probability_se,
                    "calibration_error": calibration_error,
                    "data_quality_probability_error": quality_error,
                    "commission_rate": commission_rate,
                    "expected_slippage_rate": slippage_rate,
                },
                "bookmaker_margin_handling": (
                    "embedded_in_executable_odds_and_devig_market_prior"
                ),
                "staking": {
                    "method": "flat",
                    "maximum_bankroll_fraction": cfg.maximum_flat_stake_fraction,
                    "kelly_allowed": False,
                },
                "data_quality_score": quality,
                "market_period": forecast.get("market_period") or "REGULATION_90_MINUTES",
                "status": "PAPER_ONLY",
                "real_money_eligible": False,
            })
        if not match_rows:
            reject("no_candidate_survived_strict_filter")
            continue
        match_rows.sort(
            key=lambda row: (-row["ev_lcb95"], -row["point_edge"], str(row["selection"]))
        )
        eligible.append(match_rows[0])

    eligible.sort(
        key=lambda row: (
            -row["robust_edge"],
            -row["data_quality_score"],
            str(row.get("kickoff_utc") or ""),
            row["fixture_id"],
        )
    )
    selected = eligible[: cfg.maximum_candidates]
    for rank, row in enumerate(selected, 1):
        row["rank"] = rank
    return {
        "schema_version": "paper-candidate-ranking/1.0",
        "status": "PAPER_ONLY",
        "real_money_execution": False,
        "generated_at": payload.get("generated_at"),
        "policy": asdict(cfg),
        "eligible_matches": len(eligible),
        "displayed_candidates": len(selected),
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "candidates": deepcopy(selected),
    }
