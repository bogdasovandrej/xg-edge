"""api-football (v3) adapter for competitions no free feed otherwise covers.

football-data.org's free tier does not carry the Russian Premier League or
any domestic cup, so those come from api-football, whose free plan allows 100
requests per day. Without ``API_FOOTBALL_KEY`` the adapter makes no network
call at all.

League ids are treated as unverified until the response confirms them: the
constants in ``xgedge.data.coverage`` are documented defaults, and every
fixture is checked against the league name the provider actually returned. A
mismatch fails closed rather than silently filing another country's cup under
``Кубок Испании``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

import requests

from xgedge.data.coverage import COVERAGE_BY_KEY

BASE_URL = "https://v3.football.api-sports.io"
FIXTURE_SCHEMA_VERSION = "api-football-fixtures/1.0"

# Competitions this adapter is allowed to request, keyed as in coverage.py.
SUPPORTED_KEYS = (
    "rpl", "fa_cup", "efl_cup", "copa_del_rey", "dfb_pokal",
    "coppa_italia", "coupe_de_france",
)


def _as_utc(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _name_matches(expected: str, actual: str) -> bool:
    """Loose but non-empty check that the provider returned the right league."""
    left = "".join(ch for ch in expected.casefold() if ch.isalnum())
    right = "".join(ch for ch in actual.casefold() if ch.isalnum())
    return bool(left) and bool(right) and (left in right or right in left)


def normalize_fixture(
    payload: Mapping[str, Any], *, key: str, expected_name: str
) -> dict[str, Any] | None:
    """Normalize one api-football fixture, or return None when unusable."""
    fixture = payload.get("fixture")
    league = payload.get("league")
    teams = payload.get("teams")
    if not all(isinstance(part, Mapping) for part in (fixture, league, teams)):
        return None
    actual_name = str(league.get("name") or "")
    if not _name_matches(expected_name, actual_name):
        raise ValueError(
            f"api-football returned league {actual_name!r} for {key!r}, "
            f"expected something matching {expected_name!r}; refusing to file it"
        )
    home = teams.get("home") if isinstance(teams.get("home"), Mapping) else {}
    away = teams.get("away") if isinstance(teams.get("away"), Mapping) else {}
    home_name = str(home.get("name") or "").strip()
    away_name = str(away.get("name") or "").strip()
    fixture_id = fixture.get("id")
    kickoff = fixture.get("date")
    if not fixture_id or not home_name or not away_name or not kickoff:
        return None
    try:
        kickoff_utc = _as_utc(str(kickoff))
    except ValueError:
        return None
    venue = fixture.get("venue") if isinstance(fixture.get("venue"), Mapping) else {}
    return {
        "source": "api-football",
        "id": f"apifootball:{key}:{fixture_id}",
        "provider_id": str(fixture_id),
        "competition_id": str(league.get("id") or ""),
        "competition": actual_name,
        "competition_key": key,
        "season_id": str(league.get("season") or ""),
        "kickoff_utc": _iso(kickoff_utc),
        "home_id": str(home.get("id")) if home.get("id") is not None else None,
        "home": home_name,
        "away_id": str(away.get("id")) if away.get("id") is not None else None,
        "away": away_name,
        "venue": venue.get("name"),
        "venue_city": venue.get("city"),
        "round": league.get("round"),
        "stage": "Domestic cup" if key != "rpl" else "Domestic league",
    }


def fetch_fixtures(
    *,
    api_key: str,
    keys: Iterable[str] = SUPPORTED_KEYS,
    as_of: str | datetime | None = None,
    to_date: str | datetime | None = None,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Fetch scheduled fixtures for the requested competitions.

    One request per competition, which keeps a full refresh inside the free
    plan's daily allowance. Per-competition failures are returned as errors so
    one bad league cannot empty the whole snapshot.
    """
    if not str(api_key or "").strip():
        raise ValueError("API_FOOTBALL_KEY is empty")
    requested = [key for key in keys if key in SUPPORTED_KEYS]
    unknown = [key for key in keys if key not in SUPPORTED_KEYS]
    if unknown:
        raise KeyError(f"unsupported competition keys: {sorted(unknown)}")

    now = _as_utc(as_of)
    until = _as_utc(to_date) if to_date is not None else now + timedelta(days=45)
    if until <= now:
        raise ValueError("to_date must be after as_of")

    client = session or requests.Session()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for key in requested:
        entry = COVERAGE_BY_KEY[key]
        try:
            response = client.get(
                f"{BASE_URL}/fixtures",
                params={
                    "league": entry.provider_league_id,
                    "season": now.year,
                    "from": now.date().isoformat(),
                    "to": until.date().isoformat(),
                    "status": "NS",
                },
                headers={"x-apisports-key": api_key},
                timeout=float(timeout),
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, TypeError, ValueError) as exc:
            errors.append({"competition": key, "reason": str(exc)})
            continue
        rows = payload.get("response") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            errors.append({"competition": key, "reason": "response has no fixtures array"})
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            try:
                normalized = normalize_fixture(row, key=key, expected_name=entry.name)
            except ValueError as exc:
                errors.append({"competition": key, "reason": str(exc)})
                break
            if normalized is None:
                continue
            kickoff = _as_utc(normalized["kickoff_utc"])
            if kickoff <= now or kickoff > until:
                continue
            records.append(normalized)

    records.sort(key=lambda row: (row["kickoff_utc"], row["competition"], row["id"]))
    return {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "source": "api-football",
        "generated_at": _iso(now),
        "status": "partial" if errors and records else "unavailable" if errors else "available",
        "requested_competitions": requested,
        "fixtures": records,
        "errors": errors,
    }
