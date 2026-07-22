"""Strict, deterministic ranking for PAPER-only market candidates."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping


@dataclass(frozen=True)
class PaperRankingConfig:
    """Frozen v1 selector; every penalty is expressed as expected-return points."""

    version: str = "paper-ranking-v1"
    minimum_point_edge: float = 0.03
    maximum_odds: float = 6.0
    minimum_data_quality: float = 60.0
    quality_target: float = 85.0
    maximum_quality_penalty: float = 0.04
    maximum_candidates: int = 10
    uncertainty_penalty_low: float = 0.005
    uncertainty_penalty_medium: float = 0.015
    uncertainty_penalty_high: float = 0.03
    uncertainty_penalty_unknown: float = 0.04

    def validate(self) -> None:
        numeric = (
            self.minimum_point_edge,
            self.maximum_odds,
            self.minimum_data_quality,
            self.quality_target,
            self.maximum_quality_penalty,
            self.uncertainty_penalty_low,
            self.uncertainty_penalty_medium,
            self.uncertainty_penalty_high,
            self.uncertainty_penalty_unknown,
        )
        if not all(isfinite(float(value)) for value in numeric):
            raise ValueError("paper ranking config must be finite")
        if self.minimum_point_edge < 0 or self.maximum_odds <= 1:
            raise ValueError("edge must be non-negative and maximum_odds above 1")
        if not 0 <= self.minimum_data_quality <= self.quality_target <= 100:
            raise ValueError("data-quality thresholds must satisfy 0 <= minimum <= target <= 100")
        if self.maximum_quality_penalty < 0:
            raise ValueError("maximum_quality_penalty must be non-negative")
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


def _uncertainty_penalty(label: Any, config: PaperRankingConfig) -> float:
    return {
        "low": config.uncertainty_penalty_low,
        "низкая": config.uncertainty_penalty_low,
        "medium": config.uncertainty_penalty_medium,
        "средняя": config.uncertainty_penalty_medium,
        "high": config.uncertainty_penalty_high,
        "высокая": config.uncertainty_penalty_high,
    }.get(str(label or "").strip().casefold(), config.uncertainty_penalty_unknown)


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
    probability.  It only applies a frozen uncertainty/data-quality haircut to
    point expected return and exposes the evidence used for the ranking.
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
        quality_penalty = min(
            cfg.maximum_quality_penalty,
            cfg.maximum_quality_penalty * quality_gap / quality_span,
        )
        uncertainty_penalty = _uncertainty_penalty(forecast.get("uncertainty"), cfg)
        match_rows: list[dict[str, Any]] = []
        for source in candidates:
            if not isinstance(source, Mapping):
                continue
            probability = _finite(source.get("probability"))
            odds = _finite(source.get("market_odds"))
            point_edge = _finite(source.get("point_edge"))
            if (
                probability is None
                or odds is None
                or point_edge is None
                or not 0 < probability < 1
                or not 1 < odds <= cfg.maximum_odds
                or point_edge < cfg.minimum_point_edge
            ):
                continue
            robust_edge = point_edge - quality_penalty - uncertainty_penalty
            if robust_edge <= 0:
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
                "point_edge": point_edge,
                "robust_edge": robust_edge,
                "penalties": {
                    "data_quality": quality_penalty,
                    "forecast_uncertainty": uncertainty_penalty,
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
            key=lambda row: (-row["robust_edge"], -row["point_edge"], str(row["selection"]))
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
