"""Fetch future top-five league fixtures from football-data.org v4."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

import requests


COMPETITIONS = {
    "PL": "Premier League",
    "PD": "La Liga",
    "BL1": "Bundesliga",
    "SA": "Serie A",
    "FL1": "Ligue 1",
}
BASE_URL = "https://api.football-data.org/v4"


def _as_utc(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _team(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
    identity = payload.get("id")
    name = payload.get("shortName") or payload.get("name") or payload.get("tla")
    return (str(identity) if identity is not None else None, str(name).strip() if name else None)


def fetch_top5_fixtures(
    *,
    api_key: str,
    as_of: str | datetime | None = None,
    to_date: str | datetime | None = None,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    now = _as_utc(as_of if isinstance(as_of, str) or as_of is None else _iso(as_of))
    until = (
        _as_utc(to_date if isinstance(to_date, str) or to_date is None else _iso(to_date))
        if to_date is not None else now + timedelta(days=45)
    )
    if until <= now:
        raise ValueError("to_date must be after as_of")
    if not api_key.strip():
        raise ValueError("FOOTBALL_DATA_API_KEY is empty")
    client = session or requests.Session()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for code, fallback_name in COMPETITIONS.items():
        try:
            response = client.get(
                f"{BASE_URL}/competitions/{code}/matches",
                params={
                    "status": "SCHEDULED",
                    "dateFrom": now.date().isoformat(),
                    "dateTo": until.date().isoformat(),
                },
                headers={"X-Auth-Token": api_key},
                timeout=float(timeout),
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, TypeError, ValueError) as exc:
            errors.append({"competition": code, "reason": str(exc)})
            continue
        matches = payload.get("matches") if isinstance(payload, Mapping) else None
        if not isinstance(matches, list):
            errors.append({"competition": code, "reason": "response has no matches array"})
            continue
        for match in matches:
            if not isinstance(match, Mapping):
                continue
            kickoff = match.get("utcDate")
            try:
                kickoff_utc = _as_utc(str(kickoff))
            except ValueError:
                continue
            if kickoff_utc <= now or kickoff_utc > until:
                continue
            home_id, home = _team(match.get("homeTeam") if isinstance(match.get("homeTeam"), Mapping) else {})
            away_id, away = _team(match.get("awayTeam") if isinstance(match.get("awayTeam"), Mapping) else {})
            match_id = match.get("id")
            if match_id is None or not home or not away:
                continue
            competition = match.get("competition") if isinstance(match.get("competition"), Mapping) else {}
            records.append({
                "source": "football-data.org",
                "id": f"fdorg:{code}:{match_id}",
                "provider_id": str(match_id),
                "competition_id": code,
                "competition": str(competition.get("name") or fallback_name),
                "season_id": str((match.get("season") or {}).get("id") or "2026-27")
                if isinstance(match.get("season"), Mapping) else "2026-27",
                "kickoff_utc": _iso(kickoff_utc),
                "home_id": home_id,
                "home": home,
                "away_id": away_id,
                "away": away,
                "venue": None,
                "venue_city": None,
                "round": f"Matchday {match.get('matchday')}" if match.get("matchday") else None,
                "stage": "Domestic league",
            })
    records.sort(key=lambda row: (row["kickoff_utc"], row["competition"], row["id"]))
    return {
        "schema_version": "top-five-fixtures/1.0",
        "source": "football-data.org",
        "generated_at": _iso(now),
        "status": "partial" if errors and records else "unavailable" if errors else "available",
        "fixtures": records,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/live/top5_fixtures.json"))
    parser.add_argument("--as-of")
    parser.add_argument("--to-date")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    if not api_key.strip():
        print("FOOTBALL_DATA_API_KEY is not configured; top-five fixture refresh skipped")
        return
    document = fetch_top5_fixtures(
        api_key=api_key,
        as_of=args.as_of,
        to_date=args.to_date,
        timeout=args.timeout,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(document['fixtures'])} top-five fixtures to {args.output}")


if __name__ == "__main__":
    main()
