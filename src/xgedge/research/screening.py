"""Deterministic lightweight screening of every future official fixture."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

from xgedge.markets.taxonomy import market_family


@dataclass(frozen=True, slots=True)
class ResearchScreeningConfig:
    version: str = "research-priority/1.0"
    preline_pool_size: int = 20
    exploration_slots: int = 3
    data_quality_weight: float = 0.25
    scenario_clarity_weight: float = 0.20
    model_stability_weight: float = 0.15
    market_breadth_weight: float = 0.15
    context_completeness_weight: float = 0.10
    expected_liquidity_weight: float = 0.10
    diversity_bonus_weight: float = 0.05
    missing_data_penalty: float = 2.0
    extreme_tail_risk_penalty: float = 8.0
    unknown_lineup_penalty: float = 5.0
    unreliable_fixture_identity_penalty: float = 20.0

    def validate(self) -> None:
        if self.preline_pool_size < 1:
            raise ValueError("preline_pool_size must be positive")
        if not 0 <= self.exploration_slots <= self.preline_pool_size:
            raise ValueError("exploration_slots must fit inside the preline pool")
        weights = (
            self.data_quality_weight,
            self.scenario_clarity_weight,
            self.model_stability_weight,
            self.market_breadth_weight,
            self.context_completeness_weight,
            self.expected_liquidity_weight,
            self.diversity_bonus_weight,
        )
        if any(not isfinite(value) or value < 0 for value in weights):
            raise ValueError("research weights must be finite and non-negative")
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("research weights must sum to one")


def _utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if isfinite(parsed) else default


def _quality(forecast: Mapping[str, Any]) -> float:
    details = forecast.get("details")
    if not isinstance(details, Mapping):
        return 0.0
    data = details.get("data_quality")
    return max(0.0, min(100.0, _number(data.get("score") if isinstance(data, Mapping) else 0)))


def _context(forecast: Mapping[str, Any]) -> tuple[float, list[str]]:
    details = forecast.get("details")
    availability = details.get("context_availability") if isinstance(details, Mapping) else None
    if not isinstance(availability, Mapping) or not availability:
        return 0.0, ["context_availability"]
    known = 0
    missing: list[str] = []
    for name, row in availability.items():
        status = row.get("status") if isinstance(row, Mapping) else None
        if status in {"available", "confirmed", "partial"}:
            known += 1
        else:
            missing.append(str(name))
    return 100.0 * known / len(availability), missing


def _market_breadth(forecast: Mapping[str, Any]) -> tuple[float, set[str]]:
    rows = forecast.get("model_market_forecasts")
    # A market we failed to price (SOURCE_GAP) is recorded for transparency
    # but must not inflate the breadth score as if it had been covered.
    families = {
        market_family(row.get("market"))
        for row in rows
        if isinstance(rows, list)
        and isinstance(row, Mapping)
        and row.get("status") != "SOURCE_GAP"
    } if isinstance(rows, list) else set()
    families.discard("UNKNOWN")
    return min(100.0, len(families) / 7.0 * 100.0), families


def _liquidity(competition: object) -> float:
    text = str(competition or "").casefold()
    if any(token in text for token in ("champions league", "europa league", "conference league")):
        return 70.0
    if any(token in text for token in ("premier league", "laliga", "bundesliga", "serie a", "ligue 1")):
        return 90.0
    return 45.0


def _record(forecast: Mapping[str, Any], cfg: ResearchScreeningConfig) -> dict[str, Any]:
    fixture_id = str(forecast.get("id") or "").strip()
    kickoff = _utc(forecast.get("kickoff_utc"))
    quality = _quality(forecast)
    context, missing = _context(forecast)
    breadth, families = _market_breadth(forecast)
    details = forecast.get("details") if isinstance(forecast.get("details"), Mapping) else {}
    tail = details.get("tail_risk") if isinstance(details, Mapping) else None
    tail_risk = max(0.0, min(100.0, _number(tail.get("score") if isinstance(tail, Mapping) else 100)))
    uncertainty = str(forecast.get("uncertainty") or "unknown").casefold()
    stability = {"low": 85.0, "низкая": 85.0, "medium": 65.0, "средняя": 65.0,
                 "high": 35.0, "высокая": 35.0}.get(uncertainty, 40.0)
    scenario_clarity = max(0.0, 100.0 - tail_risk)
    liquidity = _liquidity(forecast.get("competition"))
    diversity = 100.0 if "QUALIFICATION" in families or "TEAM_TOTALS" in families else 50.0
    contributions = {
        "data": cfg.data_quality_weight * quality,
        "scenario_clarity": cfg.scenario_clarity_weight * scenario_clarity,
        "model_stability": cfg.model_stability_weight * stability,
        "market_breadth": cfg.market_breadth_weight * breadth,
        "context": cfg.context_completeness_weight * context,
        "liquidity": cfg.expected_liquidity_weight * liquidity,
        "diversity_bonus": cfg.diversity_bonus_weight * diversity,
    }
    penalties = {
        "missing_data": min(20.0, len(missing) * cfg.missing_data_penalty),
        "extreme_tail_risk": cfg.extreme_tail_risk_penalty if tail_risk >= 75 else 0.0,
        "unknown_lineup": cfg.unknown_lineup_penalty if "lineups" in missing else 0.0,
        "unreliable_fixture_identity": (
            cfg.unreliable_fixture_identity_penalty if not fixture_id or kickoff is None else 0.0
        ),
    }
    score = max(0.0, min(100.0, sum(contributions.values()) - sum(penalties.values())))
    reasons = [
        name for name, value in contributions.items() if value >= 8.0
    ] + [f"penalty:{name}" for name, value in penalties.items() if value > 0]
    first_leg = forecast.get("first_leg") if isinstance(forecast.get("first_leg"), Mapping) else {}
    return {
        "fixture_id": fixture_id,
        "competition": forecast.get("competition"),
        "round": forecast.get("stage"),
        "leg_number": first_leg.get("leg_number"),
        "kickoff_utc": forecast.get("kickoff_utc"),
        "home": forecast.get("home"),
        "away": forecast.get("away"),
        "venue": forecast.get("venue"),
        "neutral_venue": bool(forecast.get("neutral_venue", False)),
        "first_leg_score": first_leg.get("score"),
        "aggregate_score": first_leg.get("aggregate"),
        "data_quality": quality,
        "model_confidence": stability,
        "scenario_clarity": scenario_clarity,
        "tail_risk": tail_risk,
        "market_breadth": breadth,
        "context_completeness": context,
        "expected_market_liquidity": liquidity,
        "research_priority_score": round(score, 2),
        "decomposition": {
            **{key: round(value, 2) for key, value in contributions.items()},
            **{f"penalty_{key}": -round(value, 2) for key, value in penalties.items()},
        },
        "reason_codes": reasons,
        "missing_data": missing,
        "market_families_checked": sorted(families),
        "status": "MACHINE_SCANNED",
        "selection_lane": None,
    }


def screen_fixtures(
    forecasts: Sequence[Mapping[str, Any]],
    *,
    generated_at: str,
    config: ResearchScreeningConfig | None = None,
) -> dict[str, Any]:
    """Scan all future fixtures and select exploitation plus exploration lanes."""
    cfg = config or ResearchScreeningConfig()
    cfg.validate()
    as_of = _utc(generated_at)
    if as_of is None:
        raise ValueError("generated_at must be timezone-aware")
    records = [
        _record(row, cfg) for row in forecasts
        if isinstance(row, Mapping) and (
            _utc(row.get("kickoff_utc")) is None
            or _utc(row.get("kickoff_utc")) > as_of
        )
    ]
    records.sort(key=lambda row: (
        -row["research_priority_score"], str(row.get("kickoff_utc") or ""), row["fixture_id"]
    ))
    pool_size = min(cfg.preline_pool_size, len(records))
    exploration_count = min(cfg.exploration_slots, pool_size)
    exploitation_count = pool_size - exploration_count
    exploitation = records[:exploitation_count]
    remainder = records[exploitation_count:]
    represented = {str(row.get("competition")) for row in exploitation}
    remainder.sort(key=lambda row: (
        str(row.get("competition")) in represented,
        -row["tail_risk"],
        -row["market_breadth"],
        -row["research_priority_score"],
        row["fixture_id"],
    ))
    exploration = remainder[:exploration_count]
    selected_ids = {row["fixture_id"] for row in (*exploitation, *exploration)}
    exploration_ids = {row["fixture_id"] for row in exploration}
    for row in records:
        if row["fixture_id"] in selected_ids:
            row["status"] = "PRELINE_SELECTED"
            row["selection_lane"] = (
                "EXPLORATION" if row["fixture_id"] in exploration_ids else "EXPLOITATION"
            )
        else:
            row["status"] = "NOT_PRELINE_SELECTED"
    selected = [row for row in records if row["fixture_id"] in selected_ids]
    selected.sort(key=lambda row: (
        row["selection_lane"] == "EXPLORATION",
        -row["research_priority_score"],
        row["fixture_id"],
    ))
    return {
        "schema_version": "research-queue/1.0",
        "generated_at": generated_at,
        "policy": asdict(cfg),
        "summary": {
            "total_fixtures": len(records),
            "machine_scanned": len(records),
            "preline_selected": len(selected),
            "exploitation_slots": len(exploitation),
            "exploration_slots": len(exploration),
            "not_selected": len(records) - len(selected),
        },
        "selected_fixture_ids": [row["fixture_id"] for row in selected],
        "records": records,
    }
