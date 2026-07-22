"""Official bookmaker API adapters with strict point-in-time normalization."""
from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Iterable, Mapping

import requests

from xgedge.data.point_in_time import available_snapshot, as_utc, iso_utc, unavailable_snapshot
from xgedge.experiments.ucl_qualifying import normalize_team_name

THE_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
THE_ODDS_API_DOCS = "https://the-odds-api.com/liveapi/guides/v4/"
ODDS_API_IO_BASE = "https://api.odds-api.io/v3"
ODDS_API_IO_DOCS = "https://docs.odds-api.io/guides/fetching-odds"

SPORT_KEYS = {
    "FIFA World Cup 2026": "soccer_fifa_world_cup",
    "UEFA Champions League": "soccer_uefa_champs_league",
    "Premier League": "soccer_epl",
    "La Liga": "soccer_spain_la_liga",
    "Bundesliga": "soccer_germany_bundesliga",
    "Serie A": "soccer_italy_serie_a",
    "Ligue 1": "soccer_france_ligue_one",
}

OUTCOME_KEYS = ("home", "draw", "away")
OUTCOME_LABELS = {"home": "П1", "draw": "X", "away": "П2"}

# Explicit provider-to-official aliases only. There is deliberately no fuzzy
# matching: a missed fixture is safer than attaching a price to the wrong game.
DEFAULT_ODDS_ALIASES = {
    "Győri ETO": "ETO FC Győr",
    "Iberia Tbilisi": "Iberia 1999",
    "Inter Escaldes": "Inter Club d'Escaldes",
    "Kairat Almaty": "Kairat",
    "KuPS Kuopio": "KuPS",
    "L. Red Imps": "Lincoln Red Imps",
    "Levski Sofia": "Levski",
    "Riga": "Riga FC",
    "U. Craiova": "Universitatea Craiova",
    "Víkingur R.": "Vikingur Reykjavik",
}


@dataclass(frozen=True)
class OddsApiConfig:
    regions: str = "eu"
    # One region x one market costs one request credit.  The production poller
    # needs 1X2 prices for CLV, so totals must be explicitly opted into.
    markets: tuple[str, ...] = ("h2h",)
    odds_format: str = "decimal"
    date_format: str = "iso"
    kickoff_tolerance_hours: float = 6.0

    def validate(self) -> None:
        if not self.regions.strip():
            raise ValueError("regions must be non-empty")
        if not self.markets or any(market not in {"h2h", "totals"} for market in self.markets):
            raise ValueError("markets must contain h2h and/or totals")
        if self.odds_format != "decimal" or self.date_format != "iso":
            raise ValueError("only decimal odds and ISO dates are supported")
        if not 0 < self.kickoff_tolerance_hours <= 24:
            raise ValueError("kickoff_tolerance_hours must be in (0, 24]")


def _price(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and parsed > 1.0 else None


def _point(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _canonical(name: str, aliases: Mapping[str, str]) -> str:
    direct = normalize_team_name(name)
    alias_index = {
        normalize_team_name(source): normalize_team_name(target)
        for source, target in aliases.items()
    }
    return alias_index.get(direct, direct)


def match_provider_event(
    event: Mapping[str, Any],
    fixtures: Iterable[Mapping[str, Any]],
    *,
    aliases: Mapping[str, str] | None = None,
    tolerance_hours: float = 6.0,
) -> dict[str, Any] | None:
    """Return one exact team/time match, rejecting ambiguity."""
    home, away = event.get("home_team"), event.get("away_team")
    commence = event.get("commence_time")
    if not isinstance(home, str) or not isinstance(away, str) or not commence:
        return None
    alias_map = {**DEFAULT_ODDS_ALIASES, **dict(aliases or {})}
    home_key, away_key = _canonical(home, alias_map), _canonical(away, alias_map)
    kickoff = as_utc(commence, field="commence_time")
    tolerance = timedelta(hours=float(tolerance_hours))
    candidates = []
    for source in fixtures:
        fixture = dict(source)
        if not fixture.get("id") or not fixture.get("kickoff_utc"):
            continue
        fixture_home = _canonical(str(fixture.get("home", "")), alias_map)
        fixture_away = _canonical(str(fixture.get("away", "")), alias_map)
        if (fixture_home, fixture_away) != (home_key, away_key):
            continue
        scheduled = as_utc(fixture["kickoff_utc"], field="kickoff_utc")
        if abs(scheduled - kickoff) <= tolerance:
            candidates.append(fixture)
    return candidates[0] if len(candidates) == 1 else None


def _normalize_h2h(
    outcomes: Any, *, home: str, away: str, aliases: Mapping[str, str]
) -> dict[str, float] | None:
    if not isinstance(outcomes, list):
        return None
    expected = {_canonical(home, aliases): "home", _canonical(away, aliases): "away"}
    result: dict[str, float] = {}
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            continue
        name, value = outcome.get("name"), _price(outcome.get("price"))
        if not isinstance(name, str) or value is None:
            continue
        key = "draw" if normalize_team_name(name) in {"draw", "tie"} else expected.get(
            _canonical(name, aliases)
        )
        if key:
            result[key] = value
    return result if set(result) == {"home", "draw", "away"} else None


def _normalize_totals(outcomes: Any) -> list[dict[str, float]]:
    if not isinstance(outcomes, list):
        return []
    grouped: dict[float, dict[str, float]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            continue
        name = str(outcome.get("name", "")).strip().casefold()
        value, line = _price(outcome.get("price")), _point(outcome.get("point"))
        if name not in {"over", "under"} or value is None or line is None:
            continue
        grouped.setdefault(line, {})[name] = value
    return [
        {"line": line, "over": prices["over"], "under": prices["under"]}
        for line, prices in sorted(grouped.items())
        if set(prices) == {"over", "under"}
    ]


def normalize_odds_event(
    event: Mapping[str, Any],
    *,
    fixtures: Iterable[Mapping[str, Any]],
    snapshot_at: str | datetime,
    requested_at: str | datetime | None = None,
    aliases: Mapping[str, str] | None = None,
    tolerance_hours: float = 6.0,
) -> dict[str, Any] | None:
    if not isinstance(event, Mapping) or event.get("id") is None:
        return None
    captured = as_utc(snapshot_at, field="snapshot_at")
    requested = as_utc(
        requested_at if requested_at is not None else captured,
        field="requested_at",
    )
    if requested > captured:
        raise ValueError("requested_at cannot be after snapshot capture")
    commence = as_utc(event.get("commence_time"), field="commence_time")
    home, away = event.get("home_team"), event.get("away_team")
    if not isinstance(home, str) or not isinstance(away, str):
        return None
    fixture = match_provider_event(
        event, fixtures, aliases=aliases, tolerance_hours=tolerance_hours
    )
    alias_map = {**DEFAULT_ODDS_ALIASES, **dict(aliases or {})}
    books = []
    for raw_book in event.get("bookmakers", []):
        if not isinstance(raw_book, Mapping):
            continue
        normalized: dict[str, Any] = {}
        latest: list[datetime] = []
        for market in raw_book.get("markets", []):
            if not isinstance(market, Mapping):
                continue
            updated = market.get("last_update") or raw_book.get("last_update")
            if updated:
                parsed = as_utc(updated, field="last_update")
                if parsed > captured + timedelta(minutes=5):
                    raise ValueError("bookmaker update is after snapshot capture")
                latest.append(parsed)
            if market.get("key") == "h2h":
                values = _normalize_h2h(
                    market.get("outcomes"), home=home, away=away, aliases=alias_map
                )
                if values:
                    normalized["h2h"] = values
            elif market.get("key") == "totals":
                totals = _normalize_totals(market.get("outcomes"))
                if totals:
                    normalized["totals"] = totals
        if not normalized:
            continue
        books.append({
            "key": str(raw_book.get("key") or "unknown"),
            "title": str(raw_book.get("title") or raw_book.get("key") or "unknown"),
            "last_update": iso_utc(max(latest), field="last_update") if latest else None,
            "markets": normalized,
        })
    if not books:
        return None
    return {
        "provider": "the_odds_api",
        "source_provider": "the_odds_api",
        "status": "SHADOW_ONLY",
        "provider_event_id": str(event["id"]),
        "sport_key": str(event.get("sport_key") or ""),
        "fixture_id": str(fixture["id"]) if fixture else None,
        "match_status": "matched" if fixture else "unmatched",
        "snapshot_at": iso_utc(captured, field="snapshot_at"),
        "requested_at": iso_utc(requested, field="requested_at"),
        "received_at": iso_utc(captured, field="received_at"),
        "commence_time": iso_utc(commence, field="commence_time"),
        "home": home,
        "away": away,
        "bookmakers": books,
        "source_url": THE_ODDS_API_DOCS,
    }


def _bookmaker_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_") or "unknown"


def normalize_odds_api_io_event(
    event: Mapping[str, Any],
    *,
    fixtures: Iterable[Mapping[str, Any]],
    snapshot_at: str | datetime,
    requested_at: str | datetime | None = None,
    aliases: Mapping[str, str] | None = None,
    tolerance_hours: float = 6.0,
) -> dict[str, Any] | None:
    """Normalize the official Odds-API.io v3 response into our provider contract."""
    if not isinstance(event, Mapping):
        return None
    home, away = event.get("home"), event.get("away")
    if not isinstance(home, str) or not isinstance(away, str):
        return None
    provider_books = event.get("bookmakers")
    if not isinstance(provider_books, Mapping):
        return None
    books: list[dict[str, Any]] = []
    for title, raw_markets in provider_books.items():
        if not isinstance(title, str) or not isinstance(raw_markets, list):
            continue
        markets: list[dict[str, Any]] = []
        latest: list[datetime] = []
        for raw_market in raw_markets:
            if not isinstance(raw_market, Mapping):
                continue
            updated = raw_market.get("updatedAt")
            if updated:
                latest.append(as_utc(updated, field="updatedAt"))
            market_name = str(raw_market.get("name") or "").strip().casefold()
            raw_odds = raw_market.get("odds")
            if not isinstance(raw_odds, list):
                continue
            if market_name in {"ml", "moneyline", "match result"}:
                outcomes: list[dict[str, Any]] = []
                for row in raw_odds:
                    if not isinstance(row, Mapping):
                        continue
                    outcomes.extend([
                        {"name": home, "price": row.get("home")},
                        {"name": "Draw", "price": row.get("draw")},
                        {"name": away, "price": row.get("away")},
                    ])
                markets.append({"key": "h2h", "outcomes": outcomes})
            elif market_name in {"totals", "over/under", "over under"}:
                outcomes = []
                for row in raw_odds:
                    if not isinstance(row, Mapping):
                        continue
                    line = row.get("hdp", row.get("max"))
                    outcomes.extend([
                        {"name": "Over", "point": line, "price": row.get("over")},
                        {"name": "Under", "point": line, "price": row.get("under")},
                    ])
                markets.append({"key": "totals", "outcomes": outcomes})
        if markets:
            books.append({
                "key": _bookmaker_key(title),
                "title": title,
                "last_update": (
                    iso_utc(max(latest), field="updatedAt") if latest else None
                ),
                "markets": markets,
            })
    league = event.get("league") if isinstance(event.get("league"), Mapping) else {}
    transformed = {
        "id": event.get("id"),
        "sport_key": str(league.get("slug") or "football"),
        "commence_time": event.get("date"),
        "home_team": home,
        "away_team": away,
        "bookmakers": books,
    }
    normalized = normalize_odds_event(
        transformed,
        fixtures=fixtures,
        snapshot_at=snapshot_at,
        requested_at=requested_at,
        aliases=aliases,
        tolerance_hours=tolerance_hours,
    )
    if normalized is None:
        return None
    normalized.update({
        "provider": "odds_api_io",
        "source_provider": "odds_api_io",
        "source_url": ODDS_API_IO_DOCS,
    })
    return normalized


def _record_time(record: Mapping[str, Any]) -> datetime:
    value = record.get("received_at") or record.get("snapshot_at")
    return as_utc(value, field="received_at")


def _record_identity(
    record: Mapping[str, Any], *, default_provider: str
) -> tuple[str, str, str] | None:
    provider = str(
        record.get("source_provider") or record.get("provider") or default_provider
    ).strip()
    fixture_id = record.get("fixture_id")
    if fixture_id is not None and str(fixture_id).strip():
        return provider, "fixture", str(fixture_id)
    event_id = record.get("provider_event_id")
    if event_id is not None and str(event_id).strip():
        return provider, "event", str(event_id)
    return None


def merge_odds_snapshots(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Roll forward the newest provider observation for each fixture.

    Poll timestamps are merged independently by sport.  This prevents a poll
    of one competition from making untouched competitions look freshly
    captured, while the public TTL still expires retained fixture records.
    """
    if not isinstance(current, Mapping):
        raise TypeError("current snapshot must be a mapping")
    prior = previous if isinstance(previous, Mapping) else {}
    current_provider = str(current.get("provider") or prior.get("provider") or "").strip()
    if not current_provider:
        raise ValueError("snapshot provider must be non-empty")
    previous_provider = str(prior.get("provider") or current_provider).strip()
    if prior and previous_provider != current_provider:
        raise ValueError("cannot merge snapshots from different providers")

    newest: dict[tuple[str, str, str], tuple[datetime, int, dict[str, Any]]] = {}
    for order, document in enumerate((prior, current)):
        records = document.get("records")
        if records is None:
            continue
        if not isinstance(records, list):
            raise ValueError("available snapshot records must be a list")
        for source in records:
            if not isinstance(source, Mapping):
                continue
            identity = _record_identity(source, default_provider=current_provider)
            if identity is None:
                continue
            observed = _record_time(source)
            existing = newest.get(identity)
            if existing is None or (observed, order) >= (existing[0], existing[1]):
                newest[identity] = (observed, order, deepcopy(dict(source)))

    poll_times: dict[str, dict[str, Any]] = {}
    for document in (prior, current):
        source_times = document.get("sport_poll_times")
        if not isinstance(source_times, Mapping):
            continue
        for sport_key, raw in source_times.items():
            if not isinstance(raw, Mapping) or not raw.get("received_at"):
                continue
            row = deepcopy(dict(raw))
            received = as_utc(row["received_at"], field="received_at")
            existing = poll_times.get(str(sport_key))
            if (
                existing is None
                or received >= as_utc(existing["received_at"], field="received_at")
            ):
                poll_times[str(sport_key)] = row

    records = [item[2] for item in newest.values()]
    records.sort(
        key=lambda row: (
            str(row.get("fixture_id") or ""),
            str(row.get("provider_event_id") or ""),
        )
    )
    timestamps = [_record_time(row) for row in records]
    timestamps.extend(
        as_utc(row["received_at"], field="received_at")
        for row in poll_times.values()
    )
    if not timestamps:
        for document in (current, prior):
            if document.get("snapshot_at"):
                timestamps.append(as_utc(document["snapshot_at"], field="snapshot_at"))
                break
    if not timestamps:
        raise ValueError("snapshot_at is required when no records or poll times exist")

    captured = max(timestamps)
    if not records and current.get("status") == "unavailable":
        output = unavailable_snapshot(
            current_provider,
            str(current.get("reason") or "snapshot_unavailable"),
            snapshot_at=captured,
        )
    else:
        output = available_snapshot(
            current_provider,
            records,
            snapshot_at=captured,
        )
    current_requested = current.get("requested_sport_keys")
    requested_keys = (
        sorted({str(key) for key in current_requested})
        if isinstance(current_requested, list)
        else sorted(
            str(key)
            for key in (current.get("sport_poll_times") or {})
        )
    )
    output.update({
        "sport_poll_times": poll_times,
        "requested_sport_keys": requested_keys,
        "quota": deepcopy(current.get("quota", prior.get("quota"))),
        "errors": deepcopy(current.get("errors", [])),
        "documentation": (
            current.get("documentation")
            or prior.get("documentation")
            or (ODDS_API_IO_DOCS if current_provider == "odds_api_io" else THE_ODDS_API_DOCS)
        ),
    })
    return output


def _best_h2h_prices(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for book in record.get("bookmakers", []):
        if not isinstance(book, Mapping):
            continue
        markets = book.get("markets")
        h2h = markets.get("h2h") if isinstance(markets, Mapping) else None
        if not isinstance(h2h, Mapping):
            continue
        for outcome in OUTCOME_KEYS:
            value = _price(h2h.get(outcome))
            if value is None:
                continue
            current = best.get(outcome)
            if current is None or value > current["odds"]:
                best[outcome] = {
                    "odds": value,
                    "bookmaker_key": str(book.get("key") or "unknown"),
                    "bookmaker": str(book.get("title") or book.get("key") or "unknown"),
                }
    return best


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clear_provider_market_fields(output: dict[str, Any]) -> None:
    for forecast in output.get("forecasts", []):
        if not isinstance(forecast, dict):
            continue
        details = forecast.get("details")
        if not isinstance(details, dict):
            continue
        # ``candidate_bets`` and ``market`` may have been produced from a
        # separately audited/manual snapshot and must never be overwritten.
        # Migrate only rows carrying the unmistakable signature of the legacy
        # provider writer; ambiguous/manual rows are preserved.
        legacy_candidates = details.get("candidate_bets")
        if (
            isinstance(details.get("live_odds"), Mapping)
            and isinstance(legacy_candidates, list)
            and bool(legacy_candidates)
            and all(
                isinstance(row, Mapping)
                and row.get("outcome") in OUTCOME_KEYS
                and bool(row.get("bookmaker_key"))
                for row in legacy_candidates
            )
        ):
            details.pop("candidate_bets", None)
        for field in ("live_odds", "market_snapshot", "market_candidates"):
            details.pop(field, None)
    output.pop("odds_feed", None)


def _market_snapshot_rejection(
    *,
    received: datetime,
    kickoff: datetime | None,
    forecast_generated: datetime | None,
    now: datetime,
    ttl: timedelta,
) -> tuple[str, str] | None:
    if kickoff is None:
        return "REJECTED", "missing_kickoff"
    if received >= kickoff:
        return "REJECTED", "captured_at_or_after_kickoff"
    if forecast_generated is not None and received < forecast_generated:
        return "REJECTED", "captured_before_forecast"
    if received > now:
        return "REJECTED", "captured_in_future"
    if now - received > ttl:
        return "STALE", "older_than_ttl"
    return None


def apply_odds_snapshot_to_live_payload(
    payload: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    ttl_hours: float = 2.0,
) -> dict[str, Any]:
    """Attach recent, point-in-time prices as shadow evidence only."""
    if not 0 < float(ttl_hours) <= 24:
        raise ValueError("ttl_hours must be in (0, 24]")
    output = deepcopy(dict(payload))
    _clear_provider_market_fields(output)
    provider = str(snapshot.get("provider") or "the_odds_api")
    records = snapshot.get("records")
    if snapshot.get("status") != "available" or not isinstance(records, list):
        output["odds_feed"] = {
            "source_provider": provider,
            "status": "UNAVAILABLE",
            "reason": snapshot.get("reason") or "snapshot_unavailable",
            "matched_forecasts": 0,
        }
        return output
    boundary = as_utc(now if now is not None else _utc_now(), field="now")
    ttl = timedelta(hours=float(ttl_hours))
    by_fixture: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping) or not record.get("fixture_id"):
            continue
        fixture_id = str(record["fixture_id"])
        try:
            observed = _record_time(record)
        except (TypeError, ValueError):
            continue
        existing = by_fixture.get(fixture_id)
        if existing is None or observed >= _record_time(existing):
            by_fixture[fixture_id] = record
    matched = 0
    rejected = 0
    stale = 0
    for forecast in output.get("forecasts", []):
        if not isinstance(forecast, dict):
            continue
        record = by_fixture.get(str(forecast.get("id") or ""))
        if record is None:
            continue
        details = forecast.get("details")
        if not isinstance(details, dict):
            details = {}
            forecast["details"] = details
        source_provider = str(
            record.get("source_provider") or record.get("provider") or provider
        )
        source_url = record.get("source_url") or (
            ODDS_API_IO_DOCS if source_provider == "odds_api_io" else THE_ODDS_API_DOCS
        )
        try:
            received = _record_time(record)
        except (TypeError, ValueError):
            details["market_snapshot"] = {
                "source_provider": source_provider,
                "status": "REJECTED",
                "reason": "missing_received_at",
                "captured_at_utc": None,
                "source_url": source_url,
            }
            details["market_candidates"] = []
            rejected += 1
            continue
        try:
            kickoff = as_utc(forecast["kickoff_utc"], field="kickoff_utc")
        except (KeyError, TypeError, ValueError):
            kickoff = None
        generated_value = (
            forecast.get("forecast_generated_at")
            or forecast.get("generated_at")
            or output.get("generated_at")
        )
        try:
            generated = (
                as_utc(generated_value, field="generated_at")
                if generated_value is not None
                else None
            )
        except (TypeError, ValueError):
            generated = None
        rejection = _market_snapshot_rejection(
            received=received,
            kickoff=kickoff,
            forecast_generated=generated,
            now=boundary,
            ttl=ttl,
        )
        if rejection is not None:
            status, reason = rejection
            details["market_snapshot"] = {
                "source_provider": source_provider,
                "status": status,
                "reason": reason,
                "captured_at_utc": iso_utc(received, field="received_at"),
                "source_url": source_url,
            }
            details["market_candidates"] = []
            stale += int(status == "STALE")
            rejected += int(status != "STALE")
            continue
        best = _best_h2h_prices(record)
        if set(best) != set(OUTCOME_KEYS):
            details["market_snapshot"] = {
                "source_provider": source_provider,
                "status": "REJECTED",
                "reason": "incomplete_1x2",
                "captured_at_utc": iso_utc(received, field="received_at"),
                "source_url": source_url,
            }
            details["market_candidates"] = []
            rejected += 1
            continue
        probabilities = {
            "home": forecast.get("p_home"),
            "draw": forecast.get("p_draw"),
            "away": forecast.get("p_away"),
        }
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(float(value))
            and float(value) > 0
            for value in probabilities.values()
        ):
            details["market_snapshot"] = {
                "source_provider": source_provider,
                "status": "REJECTED",
                "reason": "invalid_forecast_probabilities",
                "captured_at_utc": iso_utc(received, field="received_at"),
                "source_url": source_url,
            }
            details["market_candidates"] = []
            rejected += 1
            continue
        total = sum(float(value) for value in probabilities.values())
        candidates = []
        for outcome in OUTCOME_KEYS:
            probability = float(probabilities[outcome]) / total
            price = best[outcome]
            edge = probability * float(price["odds"]) - 1.0
            candidates.append({
                "selection": OUTCOME_LABELS[outcome],
                "outcome": outcome,
                "probability": probability,
                "fair_odds": 1.0 / probability,
                "market_odds": price["odds"],
                "bookmaker": price["bookmaker"],
                "bookmaker_key": price["bookmaker_key"],
                "point_edge": edge,
                "source_provider": source_provider,
                "status": "SHADOW_ONLY",
                "edge_status": (
                    "POSITIVE_SHADOW_EDGE" if edge > 0.03 else "BELOW_EDGE_THRESHOLD"
                ),
            })
        candidates.sort(key=lambda row: (-row["point_edge"], OUTCOME_KEYS.index(row["outcome"])))
        for rank, candidate in enumerate(candidates, 1):
            candidate["rank"] = rank
        details["market_snapshot"] = {
            "source_provider": source_provider,
            "status": "SHADOW_ONLY",
            "reason": None,
            "captured_at_utc": iso_utc(received, field="received_at"),
            "bookmakers": len(record.get("bookmakers", [])),
            "best_1x2": best,
            "source_url": source_url,
        }
        details["market_candidates"] = candidates
        matched += 1
    output["odds_feed"] = {
        "source_provider": provider,
        "status": "SHADOW_ONLY" if matched else "NO_ELIGIBLE_PREMATCH_SNAPSHOT",
        "snapshot_at": snapshot.get("snapshot_at"),
        "matched_forecasts": matched,
        "rejected_forecasts": rejected,
        "stale_forecasts": stale,
        "requested_sport_keys": snapshot.get("requested_sport_keys", []),
        "sport_poll_times": deepcopy(snapshot.get("sport_poll_times", {})),
        "quota": deepcopy(snapshot.get("quota")),
    }
    return output


class TheOddsApiProvider:
    """Read-only official adapter; never calls the network without a key."""

    name = "the_odds_api"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = THE_ODDS_API_BASE,
        config: OddsApiConfig | None = None,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("THE_ODDS_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.config = config or OddsApiConfig()
        self.config.validate()
        self.timeout = timeout
        self.session = session

    def fetch_snapshot(
        self,
        *,
        sport_keys: Iterable[str],
        fixtures: Iterable[Mapping[str, Any]],
        snapshot_at: str | datetime | None = None,
        aliases: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        deterministic_clock = (
            as_utc(snapshot_at, field="snapshot_at")
            if snapshot_at is not None
            else None
        )
        if not self.api_key:
            return unavailable_snapshot(
                self.name,
                "missing_api_key",
                snapshot_at=deterministic_clock or _utc_now(),
            )
        client = self.session or requests.Session()
        fixture_rows = [dict(row) for row in fixtures]
        records: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        poll_times: dict[str, dict[str, str]] = {}
        quota: dict[str, int | None] = {"remaining": None, "used": None, "last": None}
        unique_keys = sorted({str(key).strip() for key in sport_keys if str(key).strip()})
        for sport_key in unique_keys:
            requested = deterministic_clock or _utc_now()
            received: datetime | None = None
            try:
                response = client.get(
                    f"{self.base_url}/sports/{sport_key}/odds",
                    params={
                        "apiKey": self.api_key,
                        "regions": self.config.regions,
                        "markets": ",".join(self.config.markets),
                        "oddsFormat": self.config.odds_format,
                        "dateFormat": self.config.date_format,
                    },
                    headers={"Accept": "application/json", "User-Agent": "xgedge-odds/1"},
                    timeout=self.timeout,
                )
                # Capture receipt immediately after the blocking HTTP call,
                # before response parsing or normalization can add latency.
                received = deterministic_clock or _utc_now()
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("odds response must be a list")
                headers = getattr(response, "headers", {})
                for source, target in (
                    ("x-requests-remaining", "remaining"),
                    ("x-requests-used", "used"),
                    ("x-requests-last", "last"),
                ):
                    value = headers.get(source) if hasattr(headers, "get") else None
                    try:
                        quota[target] = int(value) if value is not None else quota[target]
                    except (TypeError, ValueError):
                        pass
                for event in payload:
                    if not isinstance(event, Mapping):
                        continue
                    normalized = normalize_odds_event(
                        event,
                        fixtures=fixture_rows,
                        snapshot_at=received,
                        requested_at=requested,
                        aliases=aliases,
                        tolerance_hours=self.config.kickoff_tolerance_hours,
                    )
                    if normalized:
                        records.append(normalized)
            except requests.HTTPError as exc:
                # HTTPError text may contain the full request URL, including
                # the query-string API key. Persist only a safe status code.
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)
                errors.append({
                    "sport_key": sport_key,
                    "error": f"HTTPError: status={status if status is not None else 'unknown'}",
                })
            except requests.RequestException as exc:
                errors.append({"sport_key": sport_key, "error": type(exc).__name__})
            except (ValueError, TypeError) as exc:
                errors.append({"sport_key": sport_key, "error": f"{type(exc).__name__}: {exc}"})
            finally:
                if received is None:
                    received = deterministic_clock or _utc_now()
                poll_times[sport_key] = {
                    "requested_at": iso_utc(requested, field="requested_at"),
                    "received_at": iso_utc(received, field="received_at"),
                    "status": (
                        "unavailable"
                        if any(error["sport_key"] == sport_key for error in errors)
                        else "available"
                    ),
                }
        top_captured = (
            max(
                as_utc(row["received_at"], field="received_at")
                for row in poll_times.values()
            )
            if poll_times
            else deterministic_clock or _utc_now()
        )
        if not records and errors:
            result = unavailable_snapshot(
                self.name, "all_sport_requests_failed", snapshot_at=top_captured
            )
            result.update({
                "errors": errors,
                "sport_poll_times": poll_times,
                "requested_sport_keys": unique_keys,
            })
            return result
        result = available_snapshot(self.name, records, snapshot_at=top_captured)
        result.update({
            "quota": quota,
            "errors": errors,
            "documentation": THE_ODDS_API_DOCS,
            "sport_poll_times": poll_times,
            "requested_sport_keys": unique_keys,
        })
        return result


@dataclass(frozen=True)
class OddsApiIoConfig:
    bookmakers: tuple[str, ...] = ("Bet365", "Unibet", "Pinnacle")
    batch_size: int = 10
    kickoff_tolerance_hours: float = 6.0

    def validate(self) -> None:
        if not self.bookmakers or any(not name.strip() for name in self.bookmakers):
            raise ValueError("bookmakers must contain at least one non-empty name")
        if not 1 <= self.batch_size <= 10:
            raise ValueError("batch_size must be in [1, 10]")
        if not 0 < self.kickoff_tolerance_hours <= 24:
            raise ValueError("kickoff_tolerance_hours must be in (0, 24]")


class OddsApiIoProvider:
    """Official Odds-API.io v3 football adapter with batched pre-match odds."""

    name = "odds_api_io"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = ODDS_API_IO_BASE,
        config: OddsApiIoConfig | None = None,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("ODDS_API_IO_KEY")
        configured_books = os.getenv("ODDS_API_IO_BOOKMAKERS")
        self.config = config or OddsApiIoConfig(
            bookmakers=(
                tuple(name.strip() for name in configured_books.split(",") if name.strip())
                if configured_books else OddsApiIoConfig.bookmakers
            )
        )
        self.config.validate()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session

    @staticmethod
    def _payload_list(payload: Any, *, name: str) -> list[Any]:
        if isinstance(payload, Mapping):
            payload = payload.get("data")
        if not isinstance(payload, list):
            raise ValueError(f"{name} response must be a list")
        return payload

    @staticmethod
    def _update_quota(quota: dict[str, Any], response: Any) -> None:
        headers = getattr(response, "headers", {})
        if not hasattr(headers, "get"):
            return
        for header, field in (
            ("x-ratelimit-remaining", "remaining"),
            ("x-ratelimit-limit", "limit"),
        ):
            value = headers.get(header)
            try:
                quota[field] = int(value) if value is not None else quota[field]
            except (TypeError, ValueError):
                pass
        reset = headers.get("x-ratelimit-reset")
        if reset:
            quota["reset"] = str(reset)

    def fetch_snapshot(
        self,
        *,
        sport_keys: Iterable[str],
        fixtures: Iterable[Mapping[str, Any]],
        snapshot_at: str | datetime | None = None,
        aliases: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        deterministic_clock = (
            as_utc(snapshot_at, field="snapshot_at")
            if snapshot_at is not None else None
        )
        if not self.api_key:
            return unavailable_snapshot(
                self.name,
                "missing_api_key",
                snapshot_at=deterministic_clock or _utc_now(),
            )
        client = self.session or requests.Session()
        fixture_rows = [dict(row) for row in fixtures]
        requested_keys = sorted({
            str(key).strip() for key in sport_keys if str(key).strip()
        })
        requested = deterministic_clock or _utc_now()
        received = requested
        records: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        quota: dict[str, Any] = {"remaining": None, "limit": None, "reset": None}
        headers = {"Accept": "application/json", "User-Agent": "xgedge-odds/2"}
        try:
            future_kickoffs = [
                as_utc(row["kickoff_utc"], field="kickoff_utc")
                for row in fixture_rows if row.get("kickoff_utc")
                and as_utc(row["kickoff_utc"], field="kickoff_utc") > requested
            ]
            horizon = max(future_kickoffs) + timedelta(hours=1) if future_kickoffs else requested + timedelta(days=14)
            response = client.get(
                f"{self.base_url}/events",
                params={
                    "apiKey": self.api_key,
                    "sport": "football",
                    "status": "pending",
                    "from": iso_utc(requested, field="requested_at"),
                    "to": iso_utc(horizon, field="horizon"),
                    "limit": 5000,
                },
                headers=headers,
                timeout=self.timeout,
            )
            received = deterministic_clock or _utc_now()
            response.raise_for_status()
            self._update_quota(quota, response)
            events = self._payload_list(response.json(), name="events")
            matched_event_ids: list[str] = []
            for event in events:
                if not isinstance(event, Mapping) or event.get("id") is None:
                    continue
                candidate = {
                    "home_team": event.get("home"),
                    "away_team": event.get("away"),
                    "commence_time": event.get("date"),
                }
                if match_provider_event(
                    candidate,
                    fixture_rows,
                    aliases=aliases,
                    tolerance_hours=self.config.kickoff_tolerance_hours,
                ):
                    matched_event_ids.append(str(event["id"]))
            matched_event_ids = list(dict.fromkeys(matched_event_ids))
            for start in range(0, len(matched_event_ids), self.config.batch_size):
                batch = matched_event_ids[start:start + self.config.batch_size]
                batch_requested = deterministic_clock or _utc_now()
                try:
                    odds_response = client.get(
                        f"{self.base_url}/odds/multi",
                        params={
                            "apiKey": self.api_key,
                            "eventIds": ",".join(batch),
                            "bookmakers": ",".join(self.config.bookmakers),
                        },
                        headers=headers,
                        timeout=self.timeout,
                    )
                    batch_received = deterministic_clock or _utc_now()
                    received = max(received, batch_received)
                    odds_response.raise_for_status()
                    self._update_quota(quota, odds_response)
                    odds_events = self._payload_list(
                        odds_response.json(), name="multi-odds"
                    )
                    for event in odds_events:
                        normalized = normalize_odds_api_io_event(
                            event,
                            fixtures=fixture_rows,
                            snapshot_at=batch_received,
                            requested_at=batch_requested,
                            aliases=aliases,
                            tolerance_hours=self.config.kickoff_tolerance_hours,
                        )
                        if normalized:
                            records.append(normalized)
                except requests.HTTPError as exc:
                    response_value = getattr(exc, "response", None)
                    status = getattr(response_value, "status_code", None)
                    errors.append({
                        "sport_key": "football",
                        "error": f"HTTPError: status={status if status is not None else 'unknown'}",
                    })
                except requests.RequestException as exc:
                    errors.append({"sport_key": "football", "error": type(exc).__name__})
                except (TypeError, ValueError) as exc:
                    errors.append({
                        "sport_key": "football",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
        except requests.HTTPError as exc:
            response_value = getattr(exc, "response", None)
            status = getattr(response_value, "status_code", None)
            errors.append({
                "sport_key": "football",
                "error": f"HTTPError: status={status if status is not None else 'unknown'}",
            })
        except requests.RequestException as exc:
            errors.append({"sport_key": "football", "error": type(exc).__name__})
        except (TypeError, ValueError) as exc:
            errors.append({
                "sport_key": "football",
                "error": f"{type(exc).__name__}: {exc}",
            })

        poll_times = {
            key: {
                "requested_at": iso_utc(requested, field="requested_at"),
                "received_at": iso_utc(received, field="received_at"),
                "status": "unavailable" if errors else "available",
            }
            for key in requested_keys
        }
        if not records and errors:
            result = unavailable_snapshot(
                self.name, "football_requests_failed", snapshot_at=received
            )
        else:
            result = available_snapshot(self.name, records, snapshot_at=received)
        result.update({
            "quota": quota,
            "errors": errors,
            "documentation": ODDS_API_IO_DOCS,
            "sport_poll_times": poll_times,
            "requested_sport_keys": requested_keys,
        })
        return result
