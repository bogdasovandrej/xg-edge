"""Settlement results from the official FIFA and UEFA match feeds.

The module deliberately returns only regulation-time scores.  Match outcomes
are used for calibration metrics; they never create or modify CLV, which is a
pre-kickoff market-price measurement.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import requests

from xgedge.data.point_in_time import as_utc, iso_utc
from xgedge.international.fifa import PUBLIC_USER_AGENT, load_fifa_fixtures

UEFA_MATCH_URL = "https://match.uefa.com/v5/matches/{match_id}"
FIFA_SPORT_KEY = "soccer_fifa_world_cup"
UEFA_SPORT_KEY = "soccer_uefa_champs_league"


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def normalize_uefa_result(
    payload: Mapping[str, Any], *, expected_id: str | None = None
) -> dict[str, Any] | None:
    """Normalize a finished UEFA match using regulation time only.

    ``score.total`` can contain extra-time goals, so it is intentionally never
    used as a fallback.  A malformed finished response fails closed.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("unexpected UEFA match response")
    match_id = payload.get("id")
    if match_id is None:
        raise ValueError("UEFA match response has no id")
    identity = str(match_id)
    if expected_id is not None and identity != str(expected_id):
        raise ValueError("UEFA match response id does not match the requested fixture")
    if str(payload.get("status", "")).upper() != "FINISHED":
        return None
    score = payload.get("score")
    regular = score.get("regular") if isinstance(score, Mapping) else None
    home = _integer(regular.get("home")) if isinstance(regular, Mapping) else None
    away = _integer(regular.get("away")) if isinstance(regular, Mapping) else None
    if home is None or away is None:
        raise ValueError("finished UEFA match has no valid regulation-time score")
    return {
        "source": "uefa",
        "id": identity,
        "status": "FINISHED",
        "home_goals_90": home,
        "away_goals_90": away,
    }


def _pending_by_source(
    ledger: Mapping[str, Any], *, now: datetime
) -> tuple[set[str], set[str]]:
    fixtures = ledger.get("fixtures") if isinstance(ledger, Mapping) else None
    if not isinstance(fixtures, Mapping):
        return set(), set()
    fifa: set[str] = set()
    uefa: set[str] = set()
    for key, source in fixtures.items():
        if not isinstance(source, Mapping) or isinstance(source.get("result"), Mapping):
            continue
        fixture_id = str(source.get("fixture_id") or key).strip()
        kickoff = source.get("kickoff_utc")
        if not fixture_id or not kickoff:
            continue
        try:
            if as_utc(kickoff, field="kickoff_utc") > now:
                continue
        except (TypeError, ValueError):
            continue
        sport_key = str(source.get("sport_key") or "").casefold()
        if sport_key == FIFA_SPORT_KEY:
            fifa.add(fixture_id)
        elif sport_key == UEFA_SPORT_KEY:
            uefa.add(fixture_id)
    return fifa, uefa


def fetch_tracked_results(
    ledger: Mapping[str, Any],
    *,
    now: str | datetime | None = None,
    timeout: float = 30.0,
    session: requests.Session | None = None,
    fifa_loader: Callable[..., Mapping[str, Any]] = load_fifa_fixtures,
    uefa_match_url: str = UEFA_MATCH_URL,
) -> dict[str, Any]:
    """Fetch results only for past, unsettled fixture ids in a ledger.

    Provider and per-match errors are returned as metadata instead of raising,
    allowing scheduled automation to retain the previous valid ledger.
    """
    checked_at = as_utc(now or datetime.now(timezone.utc), field="now")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("timeout must be positive")
    fifa_ids, uefa_ids = _pending_by_source(ledger, now=checked_at)
    requested = sorted(fifa_ids | uefa_ids)
    if not requested:
        return {
            "status": "not_required",
            "checked_at": iso_utc(checked_at, field="now"),
            "requested_fixture_ids": [],
            "results": [],
            "errors": [],
        }

    if session is None:
        client = requests.Session()
        # A stale optional SOCKS environment can make requests fail before any
        # connection is attempted (common in local Windows Python installs).
        # The official feeds are public HTTPS endpoints and need no proxy.
        client.trust_env = False
    else:
        client = session
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    if fifa_ids:
        try:
            snapshot = fifa_loader(timeout=float(timeout), session=client)
            matches = snapshot.get("matches") if isinstance(snapshot, Mapping) else None
            if not isinstance(matches, list):
                raise ValueError("normalized FIFA snapshot has no matches array")
            for match in matches:
                if (
                    isinstance(match, Mapping)
                    and str(match.get("id")) in fifa_ids
                    and str(match.get("status", "")).upper() == "FINISHED"
                ):
                    home, away = match.get("home_goals_90"), match.get("away_goals_90")
                    if _integer(home) is None or _integer(away) is None:
                        continue
                    results.append({
                        "source": "fifa",
                        "id": str(match["id"]),
                        "status": "FINISHED",
                        "home_goals_90": home,
                        "away_goals_90": away,
                    })
        except (requests.RequestException, TypeError, ValueError) as exc:
            errors.append({"source": "fifa", "reason": str(exc)})

    for fixture_id in sorted(uefa_ids):
        try:
            response = client.get(
                uefa_match_url.format(match_id=fixture_id),
                headers={"Accept": "application/json", "User-Agent": PUBLIC_USER_AGENT},
                timeout=float(timeout),
            )
            response.raise_for_status()
            result = normalize_uefa_result(response.json(), expected_id=fixture_id)
            if result is not None:
                results.append(result)
        except (requests.RequestException, TypeError, ValueError, KeyError) as exc:
            errors.append({"source": "uefa", "fixture_id": fixture_id, "reason": str(exc)})

    results.sort(key=lambda row: (row["source"], row["id"]))
    status = "partial" if errors and results else "unavailable" if errors else "available"
    return {
        "status": status,
        "checked_at": iso_utc(checked_at, field="now"),
        "requested_fixture_ids": requested,
        "results": results,
        "errors": errors,
    }
