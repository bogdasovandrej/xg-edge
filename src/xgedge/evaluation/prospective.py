"""Prospective CLV ledger with frozen policies and one-shot cohort tests.

The confirmatory experiment is deliberately stricter than the diagnostic
market monitor:

* both the taken price and the closing benchmark must be Pinnacle 1X2;
* every competition/sport, model and probability basis has its own cohort;
* a cohort is tested exactly once, on its first 100 finalized candidates in
  deterministic kickoff order; and
* a global betting gate is never opened by mixing heterogeneous cohorts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite, log
from statistics import median
from typing import Any, Iterable, Mapping

import numpy as np

from xgedge.data.point_in_time import as_utc, iso_utc
from xgedge.decision.market_anchor import clv_betting_gate, devig_opening_odds

SCHEMA_VERSION = "prospective-clv/1.2"
CONFIRMATORY_HORIZON = 100
OUTCOME_KEYS = ("home", "draw", "away")

# This document is copied into every ledger and validated byte-for-byte at
# every mutation boundary. Changing any field requires a new schema/policy and
# therefore cannot silently rewrite the experiment after seeing its results.
_POLICY: dict[str, Any] = {
    "market": "1X2",
    "taken_benchmark": "pinnacle",
    "closing_benchmark": "pinnacle",
    "region": "eu",
    "odds_format": "decimal",
    "commission": "none",
    "edge_threshold": 0.03,
    "max_odds": 6.0,
    "closing_window_minutes": 60,
    "confirmatory_horizon": CONFIRMATORY_HORIZON,
    "ordering": ["kickoff_utc", "fixture_id"],
    "decision_rule": "one_shot_95pct_cluster_bootstrap_lower_ci_above_zero",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    )


POLICY_HASH = sha256(_canonical_json(_POLICY).encode("utf-8")).hexdigest()
# Kept as a compatibility import for older callers. It identifies the frozen
# policy, not a shared evaluation cohort; cohorts are now computed per model.
ACTIVE_COHORT_ID = f"policy-{POLICY_HASH[:24]}"


def _policy_document() -> dict[str, Any]:
    return deepcopy(_POLICY)


def _normalized_dimension(value: Any, *, fallback: str) -> str:
    if value is None:
        return fallback
    compact = " ".join(str(value).split()).strip()
    return compact.casefold() if compact else fallback


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and str(value).strip():
            return value
    return None


def _cohort_dimensions(
    forecast: Mapping[str, Any],
    *,
    fixture: Mapping[str, Any] | None = None,
    record: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    fixture = fixture or {}
    record = record or {}
    competition_or_sport = _first_present(
        forecast.get("competition"),
        fixture.get("competition"),
        forecast.get("sport_key"),
        fixture.get("sport_key"),
        record.get("competition"),
        record.get("sport_key"),
    )
    return {
        "competition_or_sport": _normalized_dimension(
            competition_or_sport, fallback="unknown_competition_or_sport"
        ),
        "model": _normalized_dimension(
            forecast.get("model"), fallback="unknown_model"
        ),
        "probability_basis": _normalized_dimension(
            forecast.get("probability_basis"),
            fallback="unspecified_probability_basis",
        ),
    }


def _cohort_id(dimensions: Mapping[str, Any]) -> str:
    canonical = {
        "policy_hash": POLICY_HASH,
        "dimensions": {
            "competition_or_sport": str(dimensions["competition_or_sport"]),
            "model": str(dimensions["model"]),
            "probability_basis": str(dimensions["probability_basis"]),
        },
    }
    return f"cohort-{sha256(_canonical_json(canonical).encode('utf-8')).hexdigest()[:24]}"


def cohort_id_for_forecast(forecast: Mapping[str, Any]) -> str:
    """Return the stable policy-bound cohort id for a public forecast."""
    return _cohort_id(_cohort_dimensions(forecast))


def _pending_decision() -> dict[str, Any]:
    return {
        "status": "pending",
        "locked": False,
        "horizon": CONFIRMATORY_HORIZON,
        "locked_at": None,
        "fixture_ids": [],
        "action": "NO BET",
        "reason": "confirmatory_horizon_not_reached",
        "clv": None,
    }


def _register_cohort(
    ledger: dict[str, Any], dimensions: Mapping[str, Any]
) -> str:
    normalized = {
        "competition_or_sport": str(dimensions["competition_or_sport"]),
        "model": str(dimensions["model"]),
        "probability_basis": str(dimensions["probability_basis"]),
    }
    cohort_id = _cohort_id(normalized)
    expected = {
        "id": cohort_id,
        "policy_hash": POLICY_HASH,
        "dimensions": normalized,
        "decision": _pending_decision(),
    }
    existing = ledger["cohorts"].get(cohort_id)
    if existing is None:
        ledger["cohorts"][cohort_id] = expected
    elif (
        existing.get("policy_hash") != POLICY_HASH
        or existing.get("dimensions") != normalized
    ):
        raise ValueError("cohort id collision or mixed cohort dimensions")
    return cohort_id


def new_ledger(*, updated_at: str | datetime | None = None) -> dict[str, Any]:
    captured = updated_at or datetime.now(timezone.utc)
    output = {
        "schema_version": SCHEMA_VERSION,
        "policy_hash": POLICY_HASH,
        "policy": _policy_document(),
        "updated_at": iso_utc(captured, field="updated_at"),
        "cohorts": {},
        "fixtures": {},
    }
    output["gate"] = _summary_from_validated(output)
    return output


def _validate_decision(decision: Mapping[str, Any], cohort_id: str) -> None:
    if decision.get("horizon") != CONFIRMATORY_HORIZON:
        raise ValueError(f"cohort {cohort_id} has a different confirmatory horizon")
    status = decision.get("status")
    locked = decision.get("locked")
    if status == "pending":
        if locked is not False:
            raise ValueError(f"pending cohort {cohort_id} cannot be locked")
        return
    if status not in {"pass", "fail"} or locked is not True:
        raise ValueError(f"cohort {cohort_id} has an invalid locked decision")
    fixture_ids = decision.get("fixture_ids")
    if not isinstance(fixture_ids, list) or len(fixture_ids) != CONFIRMATORY_HORIZON:
        raise ValueError(f"cohort {cohort_id} decision must freeze exactly 100 fixtures")
    if len(set(map(str, fixture_ids))) != CONFIRMATORY_HORIZON:
        raise ValueError(f"cohort {cohort_id} decision fixtures must be independent")
    if decision.get("action") not in {"BET", "NO BET"}:
        raise ValueError(f"cohort {cohort_id} has an invalid action")


def _validate_ledger(ledger: Mapping[str, Any] | None) -> dict[str, Any]:
    if ledger is None:
        return new_ledger()
    output = deepcopy(dict(ledger))
    if output.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported prospective ledger schema: {output.get('schema_version')}"
        )
    if output.get("policy_hash") != POLICY_HASH or output.get("policy") != _POLICY:
        raise ValueError("prospective policy is immutable; start a new schema instead")
    if not isinstance(output.get("fixtures"), Mapping):
        raise ValueError("prospective ledger fixtures must be an object")
    if not isinstance(output.get("cohorts"), Mapping):
        raise ValueError("prospective ledger cohorts must be an object")
    output["fixtures"] = {
        str(key): dict(value) for key, value in output["fixtures"].items()
    }
    output["cohorts"] = {
        str(key): dict(value) for key, value in output["cohorts"].items()
    }
    for cohort_id, cohort in output["cohorts"].items():
        dimensions = cohort.get("dimensions")
        if not isinstance(dimensions, Mapping) or _cohort_id(dimensions) != cohort_id:
            raise ValueError(f"cohort {cohort_id} has an invalid stable hash")
        if cohort.get("id") != cohort_id or cohort.get("policy_hash") != POLICY_HASH:
            raise ValueError(f"cohort {cohort_id} is not bound to the frozen policy")
        decision = cohort.get("decision")
        if not isinstance(decision, Mapping):
            raise ValueError(f"cohort {cohort_id} must contain a decision object")
        _validate_decision(decision, cohort_id)
    for fixture_id, entry in output["fixtures"].items():
        if str(entry.get("fixture_id")) != fixture_id:
            raise ValueError(f"fixture key/id mismatch for {fixture_id}")
        cohort_id = entry.get("evaluation_cohort_id")
        dimensions = entry.get("cohort_dimensions")
        # ``new_ledger`` is also used by offline result-import tools that may
        # insert a fully formed fixture row directly. Normalize such rows at
        # the validation boundary; their sport/model/basis still determine a
        # stable isolated cohort, so this never creates a shared global pool.
        if cohort_id is None and dimensions is None:
            forecast = entry.get("forecast")
            forecast = forecast if isinstance(forecast, Mapping) else {}
            dimensions = _cohort_dimensions(forecast, fixture=entry, record=entry)
            cohort_id = _register_cohort(output, dimensions)
            entry["evaluation_cohort_id"] = cohort_id
            entry["cohort_dimensions"] = dimensions
        if cohort_id not in output["cohorts"] or not isinstance(dimensions, Mapping):
            raise ValueError(f"fixture {fixture_id} has no registered cohort")
        if _cohort_id(dimensions) != cohort_id:
            raise ValueError(f"fixture {fixture_id} has mixed cohort dimensions")
        if output["cohorts"][cohort_id].get("dimensions") != dict(dimensions):
            raise ValueError(f"fixture {fixture_id} does not match its cohort")
    return output


def _probabilities(forecast: Mapping[str, Any]) -> dict[str, float] | None:
    values = {
        "home": forecast.get("p_home"),
        "draw": forecast.get("p_draw"),
        "away": forecast.get("p_away"),
    }
    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in values.values()
    ):
        return None
    parsed = {key: float(value) for key, value in values.items()}
    if any(not isfinite(value) or value <= 0 for value in parsed.values()):
        return None
    total = sum(parsed.values())
    return {key: value / total for key, value in parsed.items()}


def _book_h2h(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    output = []
    for book in record.get("bookmakers", []):
        if not isinstance(book, Mapping):
            continue
        markets = book.get("markets")
        h2h = markets.get("h2h") if isinstance(markets, Mapping) else None
        if not isinstance(h2h, Mapping):
            continue
        try:
            values = {key: float(h2h[key]) for key in OUTCOME_KEYS}
        except (KeyError, TypeError, ValueError):
            continue
        if any(not isfinite(value) or value <= 1 for value in values.values()):
            continue
        output.append(
            {
                "key": str(book.get("key") or "unknown"),
                "title": str(book.get("title") or book.get("key") or "unknown"),
                "last_update": book.get("last_update"),
                "odds": values,
            }
        )
    return output


def _pinnacle_book(record: Mapping[str, Any]) -> dict[str, Any] | None:
    books = [
        book for book in _book_h2h(record)
        if book["key"].casefold() == _POLICY["taken_benchmark"]
    ]
    if not books:
        return None
    # The provider normally returns one entry. This tie-break is deterministic
    # without constructing a synthetic best-price book across observations.
    return max(books, key=lambda book: str(book.get("last_update") or ""))


def select_shadow_candidate(
    probabilities: Mapping[str, float],
    record: Mapping[str, Any],
    *,
    edge_threshold: float = 0.03,
    max_odds: float = 6.0,
) -> dict[str, Any] | None:
    """Select at most one candidate from a complete Pinnacle 1X2 screen."""
    if edge_threshold != _POLICY["edge_threshold"] or max_odds != _POLICY["max_odds"]:
        raise ValueError("candidate thresholds are frozen by the prospective policy")
    book = _pinnacle_book(record)
    if book is None:
        return None
    rows = []
    for key in OUTCOME_KEYS:
        odds, probability = float(book["odds"][key]), float(probabilities[key])
        if not isfinite(probability) or probability <= 0 or odds > max_odds:
            continue
        rows.append(
            {
                "selection": key,
                "probability": probability,
                "odds": odds,
                "bookmaker_key": book["key"],
                "bookmaker": book["title"],
                "point_edge": probability * odds - 1.0,
            }
        )
    if not rows:
        return None
    chosen = max(
        rows,
        key=lambda row: (
            row["point_edge"], -OUTCOME_KEYS.index(row["selection"])
        ),
    )
    return chosen if chosen["point_edge"] > edge_threshold else None


def closing_fair_probabilities(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a confirmatory Pinnacle close or a labeled diagnostic consensus."""
    books = _book_h2h(record)
    if not books:
        return None
    sharp = next(
        (
            book for book in books
            if book["key"].casefold() == _POLICY["closing_benchmark"]
        ),
        None,
    )
    if sharp is not None:
        raw = np.asarray([[sharp["odds"][key] for key in OUTCOME_KEYS]], dtype=float)
        fair = devig_opening_odds(raw)[0]
        return {
            "method": "pinnacle_proportional_devig",
            "benchmark": "pinnacle",
            "evaluation_tier": "confirmatory",
            "bookmakers": [sharp["key"]],
            "probabilities": {
                key: float(fair[index])
                for index, key in enumerate(OUTCOME_KEYS)
            },
        }
    devigged = []
    for book in books:
        raw = np.asarray([[book["odds"][key] for key in OUTCOME_KEYS]], dtype=float)
        devigged.append(devig_opening_odds(raw)[0])
    consensus = np.asarray(
        [median([row[index] for row in devigged]) for index in range(3)]
    )
    consensus /= consensus.sum()
    return {
        "method": "median_bookmaker_proportional_devig",
        "benchmark": "non_pinnacle_consensus",
        "evaluation_tier": "diagnostic",
        "bookmakers": [book["key"] for book in books],
        "probabilities": {
            key: float(consensus[index]) for index, key in enumerate(OUTCOME_KEYS)
        },
    }


def _compact_observation(record: Mapping[str, Any], phase: str) -> dict[str, Any]:
    return {
        "snapshot_at": record["snapshot_at"],
        "phase": phase,
        "provider_event_id": record.get("provider_event_id"),
        "sport_key": record.get("sport_key"),
        "bookmakers": _book_h2h(record),
    }


def _assert_policy_parameters(
    *, closing_window_minutes: int, edge_threshold: float, max_odds: float
) -> None:
    if closing_window_minutes != _POLICY["closing_window_minutes"]:
        raise ValueError("closing window is frozen by the prospective policy")
    if edge_threshold != _POLICY["edge_threshold"]:
        raise ValueError("edge threshold is frozen by the prospective policy")
    if max_odds != _POLICY["max_odds"]:
        raise ValueError("maximum odds are frozen by the prospective policy")


def ingest_odds_snapshot(
    ledger: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any],
    *,
    fixtures: Iterable[Mapping[str, Any]],
    live_payload: Mapping[str, Any],
    closing_window_minutes: int = 60,
    edge_threshold: float = 0.03,
    max_odds: float = 6.0,
) -> dict[str, Any]:
    """Append a provider snapshot without changing the frozen experiment."""
    _assert_policy_parameters(
        closing_window_minutes=closing_window_minutes,
        edge_threshold=edge_threshold,
        max_odds=max_odds,
    )
    if snapshot.get("status") != "available" or not isinstance(
        snapshot.get("records"), list
    ):
        return _validate_ledger(ledger)
    output = (
        new_ledger(updated_at=snapshot["snapshot_at"])
        if ledger is None
        else _validate_ledger(ledger)
    )
    fixture_index = {
        str(row["id"]): dict(row)
        for row in fixtures
        if isinstance(row, Mapping) and row.get("id")
    }
    forecast_index = {
        str(row["id"]): dict(row)
        for row in live_payload.get("forecasts", [])
        if isinstance(row, Mapping) and row.get("id")
    }
    latest_capture = as_utc(snapshot["snapshot_at"], field="snapshot_at")
    for record_source in snapshot["records"]:
        if not isinstance(record_source, Mapping) or not record_source.get("fixture_id"):
            continue
        record = dict(record_source)
        fixture_id = str(record["fixture_id"])
        fixture, forecast = fixture_index.get(fixture_id), forecast_index.get(fixture_id)
        if fixture is None or forecast is None:
            continue
        captured = as_utc(record["snapshot_at"], field="snapshot_at")
        kickoff = as_utc(fixture["kickoff_utc"], field="kickoff_utc")
        if captured >= kickoff:
            continue
        until_kickoff = kickoff - captured
        dimensions = _cohort_dimensions(forecast, fixture=fixture, record=record)
        cohort_id = _register_cohort(output, dimensions)
        existing = output["fixtures"].get(fixture_id)
        if existing is None:
            existing = {
                "fixture_id": fixture_id,
                "evaluation_cohort_id": cohort_id,
                "cohort_dimensions": dimensions,
                "home": fixture.get("home"),
                "away": fixture.get("away"),
                "kickoff_utc": iso_utc(kickoff, field="kickoff_utc"),
                "provider_event_id": record.get("provider_event_id"),
                "sport_key": record.get("sport_key"),
                "forecast": {
                    "generated_at": live_payload.get("generated_at"),
                    "competition": forecast.get("competition"),
                    "model": forecast.get("model"),
                    "probability_basis": forecast.get("probability_basis"),
                    "probabilities": _probabilities(forecast),
                },
                "observations": [],
                "shadow_candidate": None,
                "closing": None,
                "result": None,
                "calibration": None,
                "clv": None,
            }
            output["fixtures"][fixture_id] = existing
        elif (
            existing.get("evaluation_cohort_id") != cohort_id
            or existing.get("cohort_dimensions") != dimensions
        ):
            raise ValueError(
                f"forecast policy changed for already tracked fixture {fixture_id}"
            )
        if not existing.get("provider_event_id"):
            existing["provider_event_id"] = record.get("provider_event_id")
        phase = (
            "closing_window"
            if until_kickoff <= timedelta(minutes=closing_window_minutes)
            else "opening" if not existing["observations"] else "monitoring"
        )
        observation = _compact_observation(record, phase)
        keys = {
            (row["snapshot_at"], row.get("provider_event_id"))
            for row in existing["observations"]
        }
        key = (observation["snapshot_at"], observation.get("provider_event_id"))
        if key not in keys:
            existing["observations"].append(observation)
            existing["observations"].sort(key=lambda row: row["snapshot_at"])
        probabilities = existing.get("forecast", {}).get("probabilities")
        if existing.get("shadow_candidate") is None and probabilities:
            candidate = select_shadow_candidate(
                probabilities,
                record,
                edge_threshold=edge_threshold,
                max_odds=max_odds,
            )
            if candidate:
                existing["shadow_candidate"] = {
                    **candidate,
                    "taken_odds": candidate["odds"],
                    "taken_at": iso_utc(captured, field="snapshot_at"),
                    "benchmark": "pinnacle",
                    "status": "SHADOW_ONLY",
                }
        finalized_status = (
            existing.get("clv", {}).get("status")
            if isinstance(existing.get("clv"), Mapping)
            else None
        )
        if phase == "closing_window" and finalized_status not in {"ready", "diagnostic"}:
            fair = closing_fair_probabilities(record)
            current_close = existing.get("closing")
            current_close_at = (
                as_utc(current_close["snapshot_at"], field="closing.snapshot_at")
                if isinstance(current_close, Mapping) and current_close.get("snapshot_at")
                else None
            )
            current_tier = (
                current_close.get("evaluation_tier")
                if isinstance(current_close, Mapping)
                else None
            )
            should_replace = bool(
                fair
                and (
                    current_close_at is None
                    or (
                        fair["evaluation_tier"] == "confirmatory"
                        and current_tier == "diagnostic"
                    )
                    or (
                        fair["evaluation_tier"] == current_tier
                        and captured > current_close_at
                    )
                )
            )
            if fair and should_replace:
                existing["closing"] = {
                    "snapshot_at": iso_utc(captured, field="snapshot_at"),
                    **fair,
                }
                candidate = existing.get("shadow_candidate")
                if candidate:
                    selection = candidate["selection"]
                    value = (
                        float(candidate["taken_odds"])
                        * float(fair["probabilities"][selection])
                        - 1.0
                    )
                    existing["clv"] = {
                        "value": value,
                        "selection": selection,
                        "taken_odds": candidate["taken_odds"],
                        "closing_fair_probability": fair["probabilities"][selection],
                        "evaluation_tier": fair["evaluation_tier"],
                        "status": "provisional",
                    }
    previous_update = output.get("updated_at")
    if previous_update:
        latest_capture = max(
            latest_capture, as_utc(previous_update, field="updated_at")
        )
    output["updated_at"] = iso_utc(latest_capture, field="updated_at")
    output["gate"] = _summary_from_validated(output)
    return output


def _ready_entries_for_cohort(
    ledger: Mapping[str, Any], cohort_id: str
) -> list[Mapping[str, Any]]:
    ready = [
        entry
        for entry in ledger["fixtures"].values()
        if entry.get("evaluation_cohort_id") == cohort_id
        and isinstance(entry.get("clv"), Mapping)
        and entry["clv"].get("status") == "ready"
        # In schema 1.2 ``ready`` is itself the confirmatory terminal state.
        # The explicit tier is retained on newly ingested rows for audit
        # readability, while direct offline imports need not duplicate it.
        and entry["clv"].get("evaluation_tier") in {None, "confirmatory"}
        and isinstance(entry["clv"].get("value"), (int, float))
        and not isinstance(entry["clv"].get("value"), bool)
        and isfinite(float(entry["clv"]["value"]))
    ]
    return sorted(
        ready,
        key=lambda entry: (
            as_utc(entry["kickoff_utc"], field="kickoff_utc"),
            str(entry["fixture_id"]),
        ),
    )


def _strict_clv_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in summary.items():
        if isinstance(value, (float, np.floating)):
            output[key] = float(value) if isfinite(float(value)) else None
        elif isinstance(value, np.integer):
            output[key] = int(value)
        else:
            output[key] = value
    return output


def _evaluate_confirmatory_horizons(
    ledger: dict[str, Any], *, locked_at: datetime
) -> bool:
    changed = False
    for cohort_id in sorted(ledger["cohorts"]):
        cohort = ledger["cohorts"][cohort_id]
        decision = cohort["decision"]
        if decision.get("locked") is True:
            continue
        ready = _ready_entries_for_cohort(ledger, cohort_id)
        if len(ready) < CONFIRMATORY_HORIZON:
            continue
        confirmatory = ready[:CONFIRMATORY_HORIZON]
        fixture_ids = [str(entry["fixture_id"]) for entry in confirmatory]
        values = [float(entry["clv"]["value"]) for entry in confirmatory]
        seed = int(cohort_id.removeprefix("cohort-")[:8], 16)
        gate = clv_betting_gate(
            values,
            fixture_ids,
            min_independent_matches=CONFIRMATORY_HORIZON,
            n_boot=10_000,
            seed=seed,
        )
        passed = gate["action"] == "BET"
        cohort["decision"] = {
            "status": "pass" if passed else "fail",
            "locked": True,
            "horizon": CONFIRMATORY_HORIZON,
            "locked_at": iso_utc(locked_at, field="locked_at"),
            "fixture_ids": fixture_ids,
            "action": gate["action"],
            "reason": gate["reason"],
            "clv": _strict_clv_summary(gate["clv"]),
        }
        changed = True
    return changed


def finalize_clv_after_kickoff(
    ledger: Mapping[str, Any], *, finalized_at: str | datetime
) -> dict[str, Any]:
    """Finalize pre-kickoff closes and run each cohort's test at most once."""
    output = _validate_ledger(ledger)
    now = as_utc(finalized_at, field="finalized_at")
    changed = False
    for entry in output["fixtures"].values():
        clv, closing = entry.get("clv"), entry.get("closing")
        if not isinstance(clv, dict) or clv.get("status") != "provisional":
            continue
        if not isinstance(closing, Mapping) or not closing.get("snapshot_at"):
            continue
        kickoff_value = entry.get("kickoff_utc")
        if not kickoff_value:
            continue
        kickoff = as_utc(kickoff_value, field="kickoff_utc")
        close_at = as_utc(closing["snapshot_at"], field="closing.snapshot_at")
        if close_at >= kickoff:
            raise ValueError("closing snapshot must be strictly before kickoff")
        if now < kickoff:
            continue
        tier = clv.get("evaluation_tier") or closing.get("evaluation_tier")
        clv["status"] = "ready" if tier == "confirmatory" else "diagnostic"
        clv["finalized_at"] = iso_utc(now, field="finalized_at")
        clv["seconds_before_kickoff"] = int((kickoff - close_at).total_seconds())
        changed = True
    decision_changed = _evaluate_confirmatory_horizons(output, locked_at=now)
    if changed or decision_changed:
        previous_update = output.get("updated_at")
        if previous_update:
            now = max(now, as_utc(previous_update, field="updated_at"))
        output["updated_at"] = iso_utc(now, field="updated_at")
    output["gate"] = _summary_from_validated(output)
    return output


def settle_results(
    ledger: Mapping[str, Any],
    results: Iterable[Mapping[str, Any]],
    *,
    settled_at: str | datetime,
) -> dict[str, Any]:
    output = _validate_ledger(ledger)
    for source in results:
        if not isinstance(source, Mapping) or str(source.get("status", "")).upper() != "FINISHED":
            continue
        fixture_id = str(source.get("id") or source.get("fixture_id") or "")
        entry = output["fixtures"].get(fixture_id)
        if entry is None:
            continue
        home, away = source.get("home_goals_90"), source.get("away_goals_90")
        if (
            isinstance(home, bool)
            or isinstance(away, bool)
            or not isinstance(home, int)
            or not isinstance(away, int)
        ):
            continue
        outcome = "home" if home > away else "away" if away > home else "draw"
        entry["result"] = {
            "home_goals_90": home,
            "away_goals_90": away,
            "outcome": outcome,
        }
        probs = entry.get("forecast", {}).get("probabilities")
        if probs:
            onehot = {key: float(key == outcome) for key in OUTCOME_KEYS}
            entry["calibration"] = {
                "logloss": -log(max(float(probs[outcome]), 1e-12)),
                "brier": sum(
                    (float(probs[key]) - onehot[key]) ** 2 for key in OUTCOME_KEYS
                ),
            }
    when = as_utc(settled_at, field="settled_at")
    if output.get("updated_at"):
        when = max(when, as_utc(output["updated_at"], field="updated_at"))
    output["updated_at"] = iso_utc(when, field="settled_at")
    output["gate"] = _summary_from_validated(output)
    return output


def _empty_clv_summary(n: int = 0) -> dict[str, Any]:
    return {
        "mean": None,
        "median": None,
        "share_positive": None,
        "ci_low": None,
        "ci_high": None,
        "n": int(n),
        "n_clusters": int(n),
        "bootstrap_unit": "cluster",
    }


def _calibration_summary(entries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    calibrated = [entry for entry in entries if entry.get("calibration")]
    return {
        "n": len(calibrated),
        "mean_logloss": (
            float(np.mean([entry["calibration"]["logloss"] for entry in calibrated]))
            if calibrated else None
        ),
        "mean_brier": (
            float(np.mean([entry["calibration"]["brier"] for entry in calibrated]))
            if calibrated else None
        ),
    }


def _summary_from_validated(ledger: Mapping[str, Any]) -> dict[str, Any]:
    entries = list(ledger["fixtures"].values())
    cohort_summaries: dict[str, Any] = {}
    for cohort_id in sorted(ledger["cohorts"]):
        cohort = ledger["cohorts"][cohort_id]
        cohort_entries = [
            entry for entry in entries
            if entry.get("evaluation_cohort_id") == cohort_id
        ]
        ready = _ready_entries_for_cohort(ledger, cohort_id)
        diagnostics = [
            entry for entry in cohort_entries
            if isinstance(entry.get("clv"), Mapping)
            and entry["clv"].get("status") == "diagnostic"
        ]
        decision = deepcopy(cohort["decision"])
        if decision.get("locked"):
            action = decision["action"]
            reason = decision["reason"]
            clv = deepcopy(decision["clv"])
        else:
            action = "NO BET"
            reason = (
                "confirmatory_horizon_not_reached"
                if len(ready) < CONFIRMATORY_HORIZON
                else "awaiting_one_shot_finalization"
            )
            # Interim outcomes are deliberately not estimated or exposed: only
            # the count is public before the pre-registered horizon is reached.
            clv = _empty_clv_summary(len(ready))
        cohort_summaries[cohort_id] = {
            "id": cohort_id,
            "dimensions": deepcopy(cohort["dimensions"]),
            "policy_hash": POLICY_HASH,
            "action": action,
            "reason": reason,
            "min_independent_matches": CONFIRMATORY_HORIZON,
            "clv": clv,
            "calibration": _calibration_summary(cohort_entries),
            "decision": decision,
            "tracked_fixtures": len(cohort_entries),
            "shadow_candidates": sum(
                bool(entry.get("shadow_candidate")) for entry in cohort_entries
            ),
            "confirmatory_ready": len(ready),
            "diagnostic_closes": len(diagnostics),
            "post_horizon_ready": (
                max(0, len(ready) - CONFIRMATORY_HORIZON)
                if decision.get("locked") else 0
            ),
            "fixture_ids": sorted(str(entry["fixture_id"]) for entry in cohort_entries),
        }
    # With exactly one cohort it is safe to expose its counts/calibration in
    # the legacy top-level slots. Two or more cohorts are never pooled.
    sole_cohort = next(iter(cohort_summaries.values()), None)
    top_clv = (
        deepcopy(sole_cohort["clv"])
        if len(cohort_summaries) == 1 else _empty_clv_summary()
    )
    top_calibration = (
        deepcopy(sole_cohort["calibration"])
        if len(cohort_summaries) == 1
        else {"n": 0, "mean_logloss": None, "mean_brier": None}
    )
    return {
        "action": "NO BET",
        "reason": "global_gate_disabled_cohort_specific_only",
        "min_independent_matches": CONFIRMATORY_HORIZON,
        "clv": top_clv,
        "calibration": top_calibration,
        "policy_hash": POLICY_HASH,
        "policy": _policy_document(),
        "cohorts": cohort_summaries,
        "cohort_count": len(cohort_summaries),
        "tracked_fixtures": len(entries),
        "shadow_candidates": sum(bool(entry.get("shadow_candidate")) for entry in entries),
        "confirmatory_ready": sum(
            cohort["confirmatory_ready"] for cohort in cohort_summaries.values()
        ),
        "diagnostic_closes": sum(
            cohort["diagnostic_closes"] for cohort in cohort_summaries.values()
        ),
    }


def prospective_summary(ledger: Mapping[str, Any]) -> dict[str, Any]:
    return _summary_from_validated(_validate_ledger(ledger))


def apply_summary_to_live_payload(
    payload: Mapping[str, Any], summary: Mapping[str, Any]
) -> dict[str, Any]:
    """Attach cohort-specific evidence while keeping the global gate closed."""
    output = deepcopy(dict(payload))
    output["prospective_clv"] = deepcopy(dict(summary))
    cohorts = summary.get("cohorts")
    cohorts = cohorts if isinstance(cohorts, Mapping) else {}
    fixture_cohorts = {
        str(fixture_id): cohort_id
        for cohort_id, cohort in cohorts.items()
        if isinstance(cohort, Mapping)
        for fixture_id in cohort.get("fixture_ids", [])
    }
    forecasts = []
    for source in output.get("forecasts", []):
        if not isinstance(source, Mapping):
            forecasts.append(source)
            continue
        forecast = dict(source)
        cohort_id = fixture_cohorts.get(
            str(forecast.get("id")), cohort_id_for_forecast(forecast)
        )
        cohort = cohorts.get(cohort_id)
        allowed = bool(
            isinstance(cohort, Mapping)
            and cohort.get("action") == "BET"
            and isinstance(cohort.get("decision"), Mapping)
            and cohort["decision"].get("status") == "pass"
            and cohort["decision"].get("locked") is True
        )
        gate = {
            "allowed": allowed,
            "action": "BET" if allowed else "NO BET",
            "reason": (
                cohort.get("reason")
                if isinstance(cohort, Mapping)
                else "cohort_not_yet_tracked"
            ),
            "decision_status": (
                cohort.get("decision", {}).get("status")
                if isinstance(cohort, Mapping)
                else "pending"
            ),
        }
        forecast["evaluation_cohort_id"] = cohort_id
        forecast["cohort_gate"] = gate
        forecast["prospective_clv"] = {"cohort_id": cohort_id, "gate": gate}
        forecasts.append(forecast)
    output["forecasts"] = forecasts
    output["betting_gate"] = {
        "allowed": False,
        "action": "NO BET",
        "reason": "global_gate_disabled_cohort_specific_only",
    }
    return output
