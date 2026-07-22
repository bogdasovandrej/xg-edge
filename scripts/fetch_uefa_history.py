"""Write official UEFA club match history for teams in a fixture snapshot."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from xgedge.data.official_feeds import (
    UEFA_CLUB_2027_SEASON_YEAR,
    UEFA_CLUB_COMPETITION_BY_KEY,
    UEFA_MATCHES_URL,
    fetch_uefa_completed_history,
    resolve_uefa_competitions,
)

DEFAULT_SEASON_YEARS = (UEFA_CLUB_2027_SEASON_YEAR, "2026")


def _datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _load_fixture_json(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("fixtures")
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError("fixture JSON must be a list or an object with a fixtures list")
    return payload


def _uefa_team_ids(fixtures: list[dict[str, Any]]) -> tuple[str, ...]:
    team_ids: set[str] = set()
    for fixture in fixtures:
        if str(fixture.get("source", "")).casefold() != "uefa":
            continue
        for key in ("home_id", "away_id"):
            value = str(fixture.get(key) or "").strip()
            if value:
                team_ids.add(value)
    if not team_ids:
        raise ValueError("fixture JSON contains no UEFA team IDs")
    return tuple(sorted(team_ids))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures-json", "--fixtures", dest="fixtures_json", type=Path, required=True
    )
    parser.add_argument(
        "--output-json", "--output", dest="output_json", type=Path, required=True
    )
    parser.add_argument("--as-of", type=_datetime, default=None)
    parser.add_argument(
        "--generated-at",
        type=_datetime,
        default=None,
        help="optional deterministic capture timestamp for reproducible snapshots",
    )
    parser.add_argument("--from-date", type=_datetime, default=None)
    parser.add_argument("--lookback-days", type=int, default=730)
    parser.add_argument(
        "--season-year",
        action="append",
        help="UEFA season year; repeatable (default: 2027 and 2026)",
    )
    parser.add_argument(
        "--uefa-competition",
        action="append",
        choices=("all", *UEFA_CLUB_COMPETITION_BY_KEY),
        help="verified UEFA competition key; repeatable (default: all)",
    )
    parser.add_argument("--uefa-url", default=UEFA_MATCHES_URL)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    as_of = args.as_of or datetime.now(timezone.utc)
    generated_at = args.generated_at or datetime.now(timezone.utc)
    season_years = tuple(args.season_year or DEFAULT_SEASON_YEARS)
    try:
        competitions = resolve_uefa_competitions(args.uefa_competition)
    except ValueError as exc:
        parser.error(str(exc))

    fixtures = _load_fixture_json(args.fixtures_json)
    team_ids = _uefa_team_ids(fixtures)
    session = requests.Session()
    session.trust_env = False
    matches = fetch_uefa_completed_history(
        team_ids=team_ids,
        competitions=competitions,
        season_years=season_years,
        as_of=as_of,
        from_date=args.from_date,
        lookback_days=args.lookback_days,
        base_url=args.uefa_url,
        page_size=args.page_size,
        max_pages=args.max_pages,
        timeout=args.timeout,
        session=session,
    )

    envelope = {
        "schema_version": "uefa-club-history/1.0",
        "generated_at_utc": _iso_utc(generated_at),
        "as_of_utc": _iso_utc(as_of),
        "status": "available",
        "scope": "club",
        "contract": {
            "match_status": "FINISHED",
            "official": True,
            "score_basis": "uefa_score_regular_90m",
            "xg": "not_provided",
        },
        "source": {"provider": "UEFA", "url": args.uefa_url},
        "competitions": [
            {
                "key": competition.key,
                "id": competition.competition_id,
                "code": competition.code,
                "name": competition.name,
            }
            for competition in competitions
        ],
        "season_years": list(season_years),
        "team_ids": list(team_ids),
        "matches": matches,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {len(matches)} official UEFA matches for {len(team_ids)} teams "
        f"to {args.output_json}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
