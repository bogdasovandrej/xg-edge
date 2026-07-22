"""Append-only archive for official fixtures, frozen forecasts and results.

The checked-in JSON file is rewritten atomically by the CLI, but its logical
records are append-only.  Every immutable record is content-addressed and each
append is linked into a hash chain.  An existing record can therefore neither
be edited nor silently removed on a later scheduled run.

Fixture schedule changes are represented by a *new* fixture snapshot.  Frozen
forecasts and final regulation-time results are never updated in place.  A
conflicting result fails closed and requires an explicit, separately versioned
correction procedure rather than rewriting model evidence.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Callable, Iterable, Mapping

from xgedge.data.official_results import (
    FIFA_SPORT_KEY,
    UEFA_SPORT_KEY,
    fetch_tracked_results,
)
from xgedge.data.point_in_time import as_utc, iso_utc
from xgedge.markets.paper_markets import SUPPORTED_SCORE_MARKETS, canonical_market

ARCHIVE_SCHEMA_VERSION = "match-evidence-archive/1.0"
ARCHIVE_MODE = "PAPER_ONLY"
EVENT_GENESIS = "0" * 64

_FIXTURE_REQUIRED = (
    "source",
    "id",
    "competition",
    "kickoff_utc",
    "home",
    "away",
)
_PROBABILITY_KEYS = ("home", "draw", "away")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _hash(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _without(record: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in record.items() if key not in keys}


def _strict_text(value: Any, *, field: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        raise ValueError(f"{field} must be non-empty")
    return text


def _fixture_key(source: Any, fixture_id: Any) -> str:
    return f"{_strict_text(source, field='source').casefold()}:{_strict_text(fixture_id, field='id')}"


def empty_archive(*, created_at: str | datetime | None = None) -> dict[str, Any]:
    when = as_utc(created_at or datetime.now(timezone.utc), field="created_at")
    timestamp = iso_utc(when, field="created_at")
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "mode": ARCHIVE_MODE,
        "created_at": timestamp,
        "updated_at": timestamp,
        "fixture_snapshots": [],
        "forecasts": [],
        "results": [],
        "events": [],
    }


def _record_hash(record: Mapping[str, Any]) -> str:
    return _hash(_without(record, "content_hash"))


def _event_hash(event: Mapping[str, Any]) -> str:
    return _hash(_without(event, "event_hash"))


def validate_archive(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate all hashes, unique identities and the append-only event chain."""
    if not isinstance(document, Mapping):
        raise ValueError("archive must be an object")
    output = deepcopy(dict(document))
    if output.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
        raise ValueError("unsupported archive schema")
    if output.get("mode") != ARCHIVE_MODE:
        raise ValueError("archive must remain PAPER_ONLY")
    created = as_utc(output.get("created_at"), field="created_at")
    updated = as_utc(output.get("updated_at"), field="updated_at")
    if updated < created:
        raise ValueError("updated_at cannot precede created_at")

    identities: dict[str, set[str]] = {
        "fixture_snapshot": set(),
        "forecast": set(),
        "result": set(),
    }
    collections = (
        ("fixture_snapshots", "fixture_snapshot", "snapshot_id"),
        ("forecasts", "forecast", "forecast_id"),
        ("results", "result", "result_id"),
    )
    record_hashes: dict[tuple[str, str], str] = {}
    for collection, entity_type, identity_field in collections:
        records = output.get(collection)
        if not isinstance(records, list):
            raise ValueError(f"{collection} must be an array")
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError(f"{collection} entries must be objects")
            identity = _strict_text(record.get(identity_field), field=identity_field)
            if identity in identities[entity_type]:
                raise ValueError(f"duplicate {identity_field}: {identity}")
            identities[entity_type].add(identity)
            expected = _record_hash(record)
            if record.get("content_hash") != expected:
                raise ValueError(f"immutable record hash mismatch: {identity}")
            record_hashes[(entity_type, identity)] = expected

    # Exactly one immutable result is allowed per official fixture.  Revisions
    # must use a future correction schema, never edit historical evidence.
    result_keys = [str(record.get("fixture_key")) for record in output["results"]]
    if len(result_keys) != len(set(result_keys)):
        raise ValueError("multiple results for one fixture are forbidden")

    events = output.get("events")
    if not isinstance(events, list):
        raise ValueError("events must be an array")
    if len(events) != len(record_hashes):
        raise ValueError("every immutable record must have exactly one event")
    previous = EVENT_GENESIS
    seen_targets: set[tuple[str, str]] = set()
    for expected_sequence, event in enumerate(events, start=1):
        if not isinstance(event, Mapping):
            raise ValueError("event entries must be objects")
        if event.get("sequence") != expected_sequence:
            raise ValueError("event sequence is not contiguous")
        if event.get("previous_event_hash") != previous:
            raise ValueError("event hash chain is broken")
        if event.get("event_hash") != _event_hash(event):
            raise ValueError("event hash mismatch")
        target = (str(event.get("entity_type")), str(event.get("entity_id")))
        if target in seen_targets:
            raise ValueError("immutable record has duplicate events")
        if record_hashes.get(target) != event.get("content_hash"):
            raise ValueError("event does not reference an immutable record")
        as_utc(event.get("appended_at"), field="event.appended_at")
        seen_targets.add(target)
        previous = str(event["event_hash"])
    if seen_targets != set(record_hashes):
        raise ValueError("event/record coverage mismatch")
    return output


def _append_record(
    archive: dict[str, Any],
    *,
    collection: str,
    entity_type: str,
    identity_field: str,
    record: Mapping[str, Any],
    appended_at: datetime,
) -> bool:
    identity = _strict_text(record.get(identity_field), field=identity_field)
    existing = {
        str(item[identity_field]): item for item in archive[collection]
    }
    if identity in existing:
        if _canonical_json(existing[identity]) != _canonical_json(record):
            raise ValueError(f"attempted rewrite of immutable {entity_type} {identity}")
        return False
    frozen = deepcopy(dict(record))
    if "content_hash" in frozen:
        raise ValueError("caller must not provide content_hash")
    frozen["content_hash"] = _record_hash(frozen)
    archive[collection].append(frozen)
    previous = (
        archive["events"][-1]["event_hash"]
        if archive["events"] else EVENT_GENESIS
    )
    event = {
        "sequence": len(archive["events"]) + 1,
        "event_type": f"{entity_type}_appended",
        "entity_type": entity_type,
        "entity_id": identity,
        "content_hash": frozen["content_hash"],
        "appended_at": iso_utc(appended_at, field="appended_at"),
        "previous_event_hash": previous,
    }
    event["event_hash"] = _event_hash(event)
    archive["events"].append(event)
    return True


def _normalize_fixture(source: Mapping[str, Any], observed_at: datetime) -> dict[str, Any]:
    missing = [key for key in _FIXTURE_REQUIRED if source.get(key) in (None, "")]
    if missing:
        raise ValueError(f"official fixture missing required fields: {', '.join(missing)}")
    provider = _strict_text(source["source"], field="source").casefold()
    if provider not in {"fifa", "uefa"}:
        raise ValueError("only official FIFA/UEFA fixtures may enter the archive")
    kickoff = as_utc(source["kickoff_utc"], field="kickoff_utc")
    fixture_id = _strict_text(source["id"], field="id")
    data = {
        key: deepcopy(value)
        for key, value in source.items()
        if key not in {"result", "score", "home_goals", "away_goals"}
    }
    data["source"] = provider
    data["id"] = fixture_id
    data["kickoff_utc"] = iso_utc(kickoff, field="kickoff_utc")
    # Provider/source and observation time are explicit, so an input filename
    # can never masquerade as primary-source provenance.
    provenance = {
        "provider": provider,
        "official": True,
        "endpoint_family": (
            "https://match.uefa.com/" if provider == "uefa"
            else "https://api.fifa.com/"
        ),
        "observed_at": iso_utc(observed_at, field="observed_at"),
    }
    identity_payload = {"fixture": data, "provenance": provenance["provider"]}
    return {
        "snapshot_id": f"fixture-{provider}-{fixture_id}-{_hash(identity_payload)[:16]}",
        "fixture_key": _fixture_key(provider, fixture_id),
        "observed_at": provenance["observed_at"],
        "fixture": data,
        "provenance": provenance,
    }


def append_fixture_snapshots(
    archive: Mapping[str, Any],
    fixtures: Iterable[Mapping[str, Any]],
    *,
    observed_at: str | datetime,
) -> tuple[dict[str, Any], int]:
    output = validate_archive(archive)
    when = as_utc(observed_at, field="observed_at")
    added = 0
    known_ids = {str(record["snapshot_id"]) for record in output["fixture_snapshots"]}
    for source in fixtures:
        if not isinstance(source, Mapping):
            raise ValueError("fixture entries must be objects")
        record = _normalize_fixture(source, when)
        # ``observed_at`` records first discovery.  Seeing the same official
        # snapshot on a later poll is idempotent, not a historical rewrite.
        if record["snapshot_id"] in known_ids:
            continue
        if _append_record(
            output,
            collection="fixture_snapshots",
            entity_type="fixture_snapshot",
            identity_field="snapshot_id",
            record=record,
            appended_at=when,
        ):
            added += 1
            known_ids.add(str(record["snapshot_id"]))
    if added:
        output["updated_at"] = iso_utc(
            max(when, as_utc(output["updated_at"], field="updated_at")),
            field="updated_at",
        )
    return validate_archive(output), added


def _fixture_index(archive: Mapping[str, Any]) -> tuple[dict[str, list[dict]], dict[str, list[str]]]:
    by_key: dict[str, list[dict]] = {}
    by_id: dict[str, list[str]] = {}
    for record in archive["fixture_snapshots"]:
        key = str(record["fixture_key"])
        by_key.setdefault(key, []).append(record)
        identity = str(record["fixture"]["id"])
        if key not in by_id.setdefault(identity, []):
            by_id[identity].append(key)
    return by_key, by_id


def _probabilities(forecast: Mapping[str, Any]) -> dict[str, float]:
    values = {
        "home": forecast.get("p_home"),
        "draw": forecast.get("p_draw"),
        "away": forecast.get("p_away"),
    }
    probabilities: dict[str, float] = {}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"forecast probability {key} is missing")
        number = float(value)
        if not isfinite(number) or not 0.0 < number < 1.0:
            raise ValueError(f"forecast probability {key} must be in (0, 1)")
        probabilities[key] = number
    if abs(sum(probabilities.values()) - 1.0) > 1e-6:
        raise ValueError("forecast 1X2 probabilities must sum to one")
    return probabilities


def _model_market_forecasts(forecast: Mapping[str, Any]) -> list[dict[str, Any]]:
    source_rows = forecast.get("model_market_forecasts")
    if source_rows is None:
        return []
    if not isinstance(source_rows, list):
        raise ValueError("model_market_forecasts must be an array")
    output: list[dict[str, Any]] = []
    for source in source_rows[:64]:
        if not isinstance(source, Mapping):
            raise ValueError("model market forecast entries must be objects")
        market = canonical_market(source.get("market"))
        if market not in SUPPORTED_SCORE_MARKETS:
            raise ValueError(f"unsupported archived market: {market}")
        selection = _strict_text(source.get("selection"), field="market.selection")
        label = _strict_text(source.get("label"), field="market.label")
        line_value = source.get("line")
        line = None if line_value is None else float(line_value)
        if line is not None and not isfinite(line):
            raise ValueError("market line must be finite")
        theoretical = float(source.get("theoretical_probability"))
        conservative = float(source.get("conservative_probability"))
        haircut = float(source.get("reliability_haircut"))
        fair = float(source.get("conservative_fair_odds"))
        if not (
            isfinite(theoretical) and 0.0 < theoretical < 1.0
            and isfinite(conservative) and 0.0 < conservative < 1.0
            and isfinite(haircut) and 0.0 <= haircut <= 0.25
            and isfinite(fair) and fair > 1.0
        ):
            raise ValueError("invalid model market probability")
        rank_value = source.get("recommendation_rank")
        rank = rank_value if isinstance(rank_value, int) and not isinstance(rank_value, bool) and rank_value > 0 else None
        output.append({
            "market": market,
            "selection": selection,
            "line": line,
            "label": label,
            "theoretical_probability": theoretical,
            "conservative_probability": conservative,
            "reliability_haircut": haircut,
            "conservative_fair_odds": fair,
            "recommendation_rank": rank,
            "status": "MODEL_ONLY_NO_BOOKMAKER_PRICE",
            "settlement_period": "REGULATION_90_MINUTES",
        })
    return output


def append_frozen_forecasts(
    archive: Mapping[str, Any],
    live_payload: Mapping[str, Any],
    *,
    archived_at: str | datetime,
) -> tuple[dict[str, Any], int, int]:
    """Append valid pre-kickoff forecasts; unmatched rows are skipped safely."""
    output = validate_archive(archive)
    when = as_utc(archived_at, field="archived_at")
    payload_generated = live_payload.get("generated_at")
    by_key, by_id = _fixture_index(output)
    added = 0
    skipped = 0
    known_forecast_ids = {
        str(record["forecast_id"]) for record in output["forecasts"]
    }
    forecasts = live_payload.get("forecasts", [])
    if not isinstance(forecasts, list):
        raise ValueError("live payload forecasts must be an array")
    for source in forecasts:
        if not isinstance(source, Mapping):
            skipped += 1
            continue
        fixture_id = str(source.get("id") or "").strip()
        keys = by_id.get(fixture_id, [])
        if len(keys) != 1:
            skipped += 1
            continue
        fixture_key = keys[0]
        snapshots = by_key[fixture_key]
        generated_value = source.get("forecast_generated_at") or payload_generated
        try:
            generated = as_utc(generated_value, field="forecast_generated_at")
            kickoff = as_utc(source.get("kickoff_utc"), field="kickoff_utc")
            probabilities = _probabilities(source)
            market_forecasts = _model_market_forecasts(source)
        except (TypeError, ValueError):
            skipped += 1
            continue
        # A timestamp embedded in a file is not enough to prove a forecast was
        # frozen.  The append-only archive must itself observe it pre-kickoff.
        if generated >= kickoff or when >= kickoff:
            skipped += 1
            continue
        fixture = snapshots[-1]["fixture"]
        if (
            str(fixture.get("home")) != str(source.get("home"))
            or str(fixture.get("away")) != str(source.get("away"))
        ):
            skipped += 1
            continue
        normalized = {
            "fixture_key": fixture_key,
            "fixture_id": fixture_id,
            "kickoff_utc": iso_utc(kickoff, field="kickoff_utc"),
            "generated_at": iso_utc(generated, field="forecast_generated_at"),
            "archived_at": iso_utc(when, field="archived_at"),
            "model": _strict_text(source.get("model"), field="model"),
            "probability_basis": str(
                source.get("probability_basis")
                or source.get("details", {}).get("probability_basis")
                or "model_1x2"
            ),
            "settlement_period": "90M",
            "probabilities": probabilities,
            "expected_goals": {
                "home": source.get("lambda_home", (source.get("expected_goals") or {}).get("home")),
                "away": source.get("lambda_away", (source.get("expected_goals") or {}).get("away")),
            },
            "model_market_forecasts": market_forecasts,
            "top_score": source.get("top_score"),
            "source_forecast_hash": _hash({
                key: source.get(key)
                for key in (
                    "id", "kickoff_utc", "model", "p_home", "p_draw", "p_away",
                    "lambda_home", "lambda_away", "top_score",
                    "model_market_forecasts",
                )
            }),
            "provenance": {
                "origin": "reports/live_predictions.json",
                "frozen_before_kickoff": True,
                "official_fixture_snapshot_id": snapshots[-1]["snapshot_id"],
            },
        }
        identity_payload = _without(normalized, "archived_at", "provenance")
        normalized["forecast_id"] = f"forecast-{_hash(identity_payload)[:24]}"
        if normalized["forecast_id"] in known_forecast_ids:
            continue
        if _append_record(
            output,
            collection="forecasts",
            entity_type="forecast",
            identity_field="forecast_id",
            record=normalized,
            appended_at=when,
        ):
            added += 1
            known_forecast_ids.add(str(normalized["forecast_id"]))
    if added:
        output["updated_at"] = iso_utc(
            max(when, as_utc(output["updated_at"], field="updated_at")),
            field="updated_at",
        )
    return validate_archive(output), added, skipped


def _latest_fixture_by_key(archive: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for record in archive["fixture_snapshots"]:
        latest[str(record["fixture_key"])] = record
    return latest


def append_official_results(
    archive: Mapping[str, Any],
    results: Iterable[Mapping[str, Any]],
    *,
    observed_at: str | datetime,
) -> tuple[dict[str, Any], int]:
    output = validate_archive(archive)
    when = as_utc(observed_at, field="observed_at")
    latest = _latest_fixture_by_key(output)
    existing = {str(row["fixture_key"]): row for row in output["results"]}
    added = 0
    for source in results:
        if not isinstance(source, Mapping):
            raise ValueError("result entries must be objects")
        provider = _strict_text(source.get("source"), field="result.source").casefold()
        fixture_id = _strict_text(
            source.get("id") or source.get("fixture_id"), field="result.id"
        )
        key = _fixture_key(provider, fixture_id)
        if key not in latest:
            continue
        if str(source.get("status", "")).upper() != "FINISHED":
            continue
        home, away = source.get("home_goals_90"), source.get("away_goals_90")
        if (
            isinstance(home, bool)
            or isinstance(away, bool)
            or not isinstance(home, int)
            or not isinstance(away, int)
            or home < 0
            or away < 0
        ):
            raise ValueError("official result requires non-negative 90M scores")
        kickoff = as_utc(latest[key]["fixture"]["kickoff_utc"], field="kickoff_utc")
        if when < kickoff:
            raise ValueError("result observation cannot precede kickoff")
        outcome = "home" if home > away else "away" if away > home else "draw"
        record = {
            "result_id": f"result-{provider}-{fixture_id}",
            "fixture_key": key,
            "fixture_id": fixture_id,
            "status": "FINISHED",
            "settlement_period": "90M",
            "home_goals_90": home,
            "away_goals_90": away,
            "outcome": outcome,
            "observed_at": iso_utc(when, field="observed_at"),
            "provenance": {
                "provider": provider,
                "official": True,
                "endpoint_family": (
                    "https://match.uefa.com/v5/matches/"
                    if provider == "uefa" else "https://api.fifa.com/"
                ),
                "score_basis": "regulation_time_only",
            },
        }
        if key in existing:
            old = existing[key]
            same = (
                old.get("home_goals_90") == home
                and old.get("away_goals_90") == away
                and old.get("settlement_period") == "90M"
            )
            if not same:
                raise ValueError(f"conflicting official result for {key}")
            continue
        if _append_record(
            output,
            collection="results",
            entity_type="result",
            identity_field="result_id",
            record=record,
            appended_at=when,
        ):
            added += 1
            existing[key] = output["results"][-1]
    if added:
        output["updated_at"] = iso_utc(
            max(when, as_utc(output["updated_at"], field="updated_at")),
            field="updated_at",
        )
    return validate_archive(output), added


def _pending_result_ledger(archive: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    settled = {str(record["fixture_key"]) for record in archive["results"]}
    fixtures: dict[str, Any] = {}
    for key, record in _latest_fixture_by_key(archive).items():
        fixture = record["fixture"]
        if key in settled or as_utc(fixture["kickoff_utc"], field="kickoff_utc") > now:
            continue
        provider = str(fixture["source"])
        fixtures[str(fixture["id"])] = {
            "fixture_id": str(fixture["id"]),
            "kickoff_utc": fixture["kickoff_utc"],
            "sport_key": FIFA_SPORT_KEY if provider == "fifa" else UEFA_SPORT_KEY,
        }
    return {"fixtures": fixtures}


def update_archive(
    archive: Mapping[str, Any] | None,
    *,
    fixtures: Iterable[Mapping[str, Any]] = (),
    live_payload: Mapping[str, Any] | None = None,
    observed_at: str | datetime | None = None,
    fetch_results: bool = False,
    timeout: float = 30.0,
    result_fetcher: Callable[..., Mapping[str, Any]] = fetch_tracked_results,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one idempotent archive cycle and return counters/health metadata."""
    when = as_utc(observed_at or datetime.now(timezone.utc), field="observed_at")
    output = validate_archive(archive) if archive is not None else empty_archive(created_at=when)
    output, fixture_count = append_fixture_snapshots(output, fixtures, observed_at=when)
    forecast_count = forecast_skipped = 0
    if live_payload is not None:
        output, forecast_count, forecast_skipped = append_frozen_forecasts(
            output, live_payload, archived_at=when
        )
    result_count = 0
    fetch_status = "not_requested"
    fetch_errors: list[Any] = []
    if fetch_results:
        snapshot = result_fetcher(
            _pending_result_ledger(output, now=when), now=when, timeout=timeout
        )
        fetch_status = str(snapshot.get("status") or "unknown")
        fetch_errors = list(snapshot.get("errors") or [])
        fetched = snapshot.get("results")
        if isinstance(fetched, list):
            output, result_count = append_official_results(
                output, fetched, observed_at=when
            )
    return validate_archive(output), {
        "fixture_snapshots_added": fixture_count,
        "forecasts_added": forecast_count,
        "forecasts_skipped": forecast_skipped,
        "results_added": result_count,
        "result_fetch_status": fetch_status,
        "result_fetch_errors": len(fetch_errors),
        "total_fixture_snapshots": len(output["fixture_snapshots"]),
        "total_forecasts": len(output["forecasts"]),
        "total_results": len(output["results"]),
        "event_chain_head": (
            output["events"][-1]["event_hash"] if output["events"] else EVENT_GENESIS
        ),
    }
