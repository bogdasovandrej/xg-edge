"""Prospective CLV v2: immutable market-residual experiment.

Version 2 is intentionally isolated from the historical Pinnacle ledger.  It
uses Betfair Exchange as the preregistered sharp benchmark, admits one stable
Top-5/1X2 market-residual cohort, and records whether the initial and closing
capture windows were actually met.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
from statistics import mean
from typing import Any, Iterable, Mapping

from xgedge.data.point_in_time import as_utc, iso_utc

SCHEMA_VERSION = "prospective-clv/2.0"
OUTCOMES = ("home", "draw", "away")
TOP_FIVE = (
    "Premier League",
    "La Liga",
    "Bundesliga",
    "Serie A",
    "Ligue 1",
)
POLICY: dict[str, Any] = {
    "primary_cohort": {
        "competitions": list(TOP_FIVE),
        "model_version": "market-residual-v1",
        "probability_basis": "betfair_exchange_residual",
        "market_family": "1X2",
        "settlement_period": "REGULATION_90_MINUTES",
    },
    "sharp_benchmark": {
        "provider": "odds_api_io_paid",
        "bookmaker_key": "betfair_exchange",
        "method": "commission_adjusted_proportional_devig",
        "commission_rate": 0.05,
        "liquidity_required": True,
    },
    "capture_schedule": {
        "initial_after_forecast_seconds": [60, 300],
        "poll_interval_minutes": 15,
        "closing_window_minutes": 60,
    },
    "selection": {
        "rule": "LCB95(execution_EV)>0",
        "z_score_one_sided_95": 1.6448536269514722,
        "default_probability_std_error": 0.02,
        "default_calibration_error": 0.015,
        "expected_slippage_rate": 0.005,
        "maximum_flat_stake_fraction": 0.0025,
        "kelly_allowed": False,
    },
    "promotion": {
        "primary_horizon": 350,
        "requires_log_loss_not_worse_than_benchmark": True,
        "requires_brier_not_worse_than_benchmark": True,
        "requires_mean_clv_above_zero": True,
        "requires_cluster_bootstrap_lcb95_above_zero": True,
        "requires_positive_replication_cohort": True,
        "roi_is_not_accuracy_evidence": True,
    },
    "real_money_execution": False,
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


POLICY_HASH = sha256(_canonical(POLICY).encode("utf-8")).hexdigest()


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _forecast_document(forecast: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "fixture_id": str(forecast["id"]),
        "competition": forecast.get("competition"),
        "model": forecast.get("model"),
        "probability_basis": forecast.get("probability_basis"),
        "generated_at": forecast.get("forecast_generated_at"),
        "probabilities": {
            outcome: forecast.get(f"p_{outcome}") for outcome in OUTCOMES
        },
    }


def _forecast_hash(forecast: Mapping[str, Any]) -> str:
    return sha256(_canonical(_forecast_document(forecast)).encode("utf-8")).hexdigest()


def _eligible_forecast(forecast: Mapping[str, Any]) -> tuple[bool, str]:
    competition = str(forecast.get("competition") or "")
    if not any(name in competition for name in TOP_FIVE):
        return False, "outside_primary_top5_cohort"
    if forecast.get("model") != POLICY["primary_cohort"]["model_version"]:
        return False, "model_version_not_primary"
    if forecast.get("probability_basis") != POLICY["primary_cohort"]["probability_basis"]:
        return False, "probability_basis_not_primary"
    probabilities = [_finite(forecast.get(f"p_{outcome}")) for outcome in OUTCOMES]
    if any(value is None or not 0 < value < 1 for value in probabilities):
        return False, "invalid_forecast_probabilities"
    if abs(sum(value for value in probabilities if value is not None) - 1.0) > 1e-6:
        return False, "forecast_probabilities_do_not_sum_to_one"
    return True, "eligible"


def _book(record: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    books = record.get("bookmakers")
    if not isinstance(books, list):
        return None
    for row in books:
        if not isinstance(row, Mapping):
            continue
        candidate = str(row.get("key") or "").casefold()
        title = str(row.get("title") or "").casefold().replace(" ", "_")
        if key in {candidate, title}:
            return row
    return None


def _h2h(book: Mapping[str, Any] | None) -> dict[str, float] | None:
    market = book.get("markets", {}).get("h2h") if isinstance(book, Mapping) else None
    if not isinstance(market, Mapping):
        return None
    output = {outcome: _finite(market.get(outcome)) for outcome in OUTCOMES}
    if any(value is None or value <= 1 for value in output.values()):
        return None
    return {key: float(value) for key, value in output.items() if value is not None}


def benchmark_probabilities(record: Mapping[str, Any]) -> dict[str, float] | None:
    """Return commission-adjusted, de-vigged Betfair Exchange probabilities."""
    book = _book(record, POLICY["sharp_benchmark"]["bookmaker_key"])
    if (
        POLICY["sharp_benchmark"]["liquidity_required"]
        and (not isinstance(book, Mapping) or book.get("liquidity_verified") is not True)
    ):
        return None
    odds = _h2h(book)
    if odds is None:
        return None
    commission = POLICY["sharp_benchmark"]["commission_rate"]
    effective = {
        outcome: 1.0 + (price - 1.0) * (1.0 - commission)
        for outcome, price in odds.items()
    }
    inverse = {outcome: 1.0 / price for outcome, price in effective.items()}
    total = sum(inverse.values())
    return {outcome: value / total for outcome, value in inverse.items()}


def _best_execution_prices(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    books = record.get("bookmakers")
    if not isinstance(books, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    benchmark_key = POLICY["sharp_benchmark"]["bookmaker_key"]
    for book in books:
        if not isinstance(book, Mapping):
            continue
        key = str(book.get("key") or "")
        if key == benchmark_key:
            continue
        odds = _h2h(book)
        if odds is None:
            continue
        for outcome, price in odds.items():
            if outcome not in result or price > result[outcome]["odds"]:
                result[outcome] = {
                    "odds": price,
                    "bookmaker": book.get("title") or key,
                    "bookmaker_key": key,
                }
    return result


def _select_candidate(
    forecast: Mapping[str, Any], record: Mapping[str, Any]
) -> dict[str, Any] | None:
    prices = _best_execution_prices(record)
    rule = POLICY["selection"]
    rows: list[dict[str, Any]] = []
    for outcome in OUTCOMES:
        probability = _finite(forecast.get(f"p_{outcome}"))
        quote = prices.get(outcome)
        if probability is None or quote is None:
            continue
        probability_lcb95 = max(
            0.0,
            probability
            - rule["z_score_one_sided_95"] * rule["default_probability_std_error"]
            - rule["default_calibration_error"],
        )
        executable_odds = 1.0 + (
            (quote["odds"] - 1.0) * (1.0 - rule["expected_slippage_rate"])
        )
        ev_lcb95 = probability_lcb95 * executable_odds - 1.0
        if ev_lcb95 > 0:
            rows.append({
                "outcome": outcome,
                "model_probability": probability,
                "probability_lcb95": probability_lcb95,
                "taken_odds": quote["odds"],
                "executable_odds_after_friction": executable_odds,
                "bookmaker": quote["bookmaker"],
                "bookmaker_key": quote["bookmaker_key"],
                "ev_lcb95": ev_lcb95,
                "stake_policy": {
                    "method": "flat",
                    "maximum_bankroll_fraction": rule["maximum_flat_stake_fraction"],
                    "kelly_allowed": False,
                },
                "status": "PAPER_ONLY",
                "real_money_eligible": False,
            })
    rows.sort(key=lambda row: (-row["ev_lcb95"], row["outcome"]))
    return rows[0] if rows else None


def new_ledger(*, updated_at: str | datetime | None = None) -> dict[str, Any]:
    at = updated_at or datetime.now(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_hash": POLICY_HASH,
        "policy": deepcopy(POLICY),
        "updated_at": iso_utc(at, field="updated_at"),
        "status": "PAPER_ONLY_WAITING_FOR_ELIGIBLE_PRIMARY_COHORT",
        "fixtures": {},
        "rejections": {},
        "alerts": [],
        "gate": {
            "allowed": False,
            "action": "NO BET",
            "reason": "primary_horizon_and_replication_not_complete",
        },
    }


def validate_ledger(ledger: Mapping[str, Any] | None) -> dict[str, Any]:
    if ledger is None:
        return new_ledger()
    output = deepcopy(dict(ledger))
    if output.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("prospective-clv/2.0 must use its own ledger")
    if output.get("policy_hash") != POLICY_HASH or output.get("policy") != POLICY:
        raise ValueError("prospective-clv/2.0 policy is immutable")
    if not isinstance(output.get("fixtures"), Mapping):
        raise ValueError("v2 fixtures must be an object")
    output["fixtures"] = {
        str(key): deepcopy(dict(value))
        for key, value in output["fixtures"].items()
        if isinstance(value, Mapping)
    }
    return output


def ingest_snapshot(
    ledger: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any],
    *,
    fixtures: Iterable[Mapping[str, Any]],
    live_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Ingest only fresh observations satisfying the frozen v2 contract."""
    output = validate_ledger(ledger)
    forecasts = {
        str(row.get("id")): row
        for row in live_payload.get("forecasts", [])
        if isinstance(row, Mapping) and row.get("id") is not None
    }
    fixture_map = {
        str(row.get("id")): row
        for row in fixtures if isinstance(row, Mapping) and row.get("id") is not None
    }
    records = snapshot.get("records")
    if snapshot.get("status") != "available" or not isinstance(records, list):
        return audit_capture_health(output, live_payload=live_payload)

    for record in records:
        if not isinstance(record, Mapping) or record.get("fixture_id") is None:
            continue
        fixture_id = str(record["fixture_id"])
        forecast = forecasts.get(fixture_id)
        fixture = fixture_map.get(fixture_id)
        if forecast is None or fixture is None:
            continue
        eligible, reason = _eligible_forecast(forecast)
        if not eligible:
            output["rejections"][reason] = output["rejections"].get(reason, 0) + 1
            continue
        captured = as_utc(
            record.get("received_at") or record.get("snapshot_at"),
            field="record.received_at",
        )
        generated = as_utc(
            forecast.get("forecast_generated_at") or live_payload.get("generated_at"),
            field="forecast_generated_at",
        )
        kickoff = as_utc(fixture.get("kickoff_utc"), field="kickoff_utc")
        if captured >= kickoff:
            continue
        probabilities = benchmark_probabilities(record)
        if probabilities is None:
            output["rejections"]["sharp_benchmark_missing"] = (
                output["rejections"].get("sharp_benchmark_missing", 0) + 1
            )
            continue
        existing = output["fixtures"].get(fixture_id)
        if existing is None:
            delay = (captured - generated).total_seconds()
            minimum, maximum = POLICY["capture_schedule"]["initial_after_forecast_seconds"]
            if delay < minimum:
                continue
            if delay > maximum:
                output["rejections"]["initial_capture_window_missed"] = (
                    output["rejections"].get("initial_capture_window_missed", 0) + 1
                )
                continue
            existing = {
                "fixture_id": fixture_id,
                "competition": forecast.get("competition"),
                "kickoff_utc": iso_utc(kickoff, field="kickoff_utc"),
                "forecast": _forecast_document(forecast),
                "forecast_hash": _forecast_hash(forecast),
                "initial_capture_at": iso_utc(captured, field="captured"),
                "candidate": _select_candidate(forecast, record),
                "observations": [],
                "closing": None,
                "clv": None,
            }
            output["fixtures"][fixture_id] = existing
        elif existing.get("forecast_hash") != _forecast_hash(forecast):
            output["rejections"]["forecast_changed_after_freeze"] = (
                output["rejections"].get("forecast_changed_after_freeze", 0) + 1
            )
            continue
        captured_iso = iso_utc(captured, field="captured")
        if not any(row.get("captured_at") == captured_iso for row in existing["observations"]):
            existing["observations"].append({
                "captured_at": captured_iso,
                "benchmark_probabilities": probabilities,
            })
            existing["observations"].sort(key=lambda row: row["captured_at"])

    output["updated_at"] = iso_utc(
        snapshot.get("snapshot_at") or datetime.now(timezone.utc),
        field="updated_at",
    )
    return audit_capture_health(output, live_payload=live_payload)


def audit_capture_health(
    ledger: Mapping[str, Any],
    *,
    live_payload: Mapping[str, Any],
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Finalize eligible closes and emit deterministic missing-window alerts."""
    output = validate_ledger(ledger)
    checked_at = as_utc(
        now or live_payload.get("generated_at") or datetime.now(timezone.utc),
        field="now",
    )
    alerts: list[dict[str, Any]] = []
    forecasts = {
        str(row.get("id")): row
        for row in live_payload.get("forecasts", [])
        if isinstance(row, Mapping) and row.get("id") is not None
    }
    minimum, maximum = POLICY["capture_schedule"]["initial_after_forecast_seconds"]
    closing_window = timedelta(minutes=POLICY["capture_schedule"]["closing_window_minutes"])
    for fixture_id, forecast in forecasts.items():
        eligible, _ = _eligible_forecast(forecast)
        if not eligible:
            continue
        generated = as_utc(
            forecast.get("forecast_generated_at") or live_payload.get("generated_at"),
            field="forecast_generated_at",
        )
        kickoff = as_utc(forecast.get("kickoff_utc"), field="kickoff_utc")
        entry = output["fixtures"].get(fixture_id)
        if entry is None and checked_at > generated + timedelta(seconds=maximum):
            alerts.append({
                "fixture_id": fixture_id,
                "kind": "initial_capture_missed",
                "severity": "error",
                "deadline": iso_utc(generated + timedelta(seconds=maximum), field="deadline"),
            })
            continue
        if entry is None:
            continue
        observations = entry.get("observations", [])
        closing_rows = [
            row for row in observations
            if kickoff - closing_window
            <= as_utc(row["captured_at"], field="captured_at")
            < kickoff
        ]
        if closing_rows:
            close = max(closing_rows, key=lambda row: row["captured_at"])
            entry["closing"] = deepcopy(close)
        if checked_at >= kickoff:
            if entry.get("closing") is None:
                alerts.append({
                    "fixture_id": fixture_id,
                    "kind": "closing_capture_missed",
                    "severity": "error",
                    "deadline": iso_utc(kickoff, field="kickoff"),
                })
            elif entry.get("clv") is None and entry.get("candidate"):
                outcome = entry["candidate"]["outcome"]
                closing_probability = entry["closing"]["benchmark_probabilities"][outcome]
                entry["clv"] = {
                    "value": (
                        entry["candidate"]["executable_odds_after_friction"]
                        * closing_probability - 1.0
                    ),
                    "status": "ready",
                    "benchmark": "betfair_exchange",
                    "finalized_at": iso_utc(checked_at, field="checked_at"),
                }
    output["alerts"] = alerts
    ready = [
        entry["clv"]["value"]
        for entry in output["fixtures"].values()
        if isinstance(entry.get("clv"), Mapping)
        and entry["clv"].get("status") == "ready"
    ]
    horizon = POLICY["promotion"]["primary_horizon"]
    output["status"] = (
        "PAPER_ONLY_COLLECTING"
        if output["fixtures"] else "PAPER_ONLY_WAITING_FOR_ELIGIBLE_PRIMARY_COHORT"
    )
    output["monitor"] = {
        "checked_at": iso_utc(checked_at, field="checked_at"),
        "eligible_enrolled": len(output["fixtures"]),
        "confirmatory_ready": len(ready),
        "horizon": horizon,
        "alerts": len(alerts),
        "mean_clv": mean(ready) if len(ready) >= horizon else None,
        "interim_inference_hidden": len(ready) < horizon,
    }
    output["gate"] = {
        "allowed": False,
        "action": "NO BET",
        "reason": (
            "replication_cohort_required_after_primary"
            if len(ready) >= horizon
            else "primary_horizon_not_reached"
        ),
    }
    output["updated_at"] = iso_utc(checked_at, field="checked_at")
    return output
