"""Fetch current official FIFA/UEFA fixtures and write JSON plus CSV snapshots."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from xgedge.data.official_feeds import (
    FIFA_CALENDAR_URL,
    FIFA_WORLD_CUP_2026_SEASON_ID,
    FIFA_WORLD_CUP_COMPETITION_ID,
    FIXTURE_FIELDS,
    UEFA_CLUB_2027_SEASON_YEAR,
    UEFA_CLUB_COMPETITION_BY_ID,
    UEFA_CLUB_COMPETITION_BY_KEY,
    UEFA_MATCHES_URL,
    fetch_fifa_fixtures,
    fetch_uefa_club_fixtures,
    resolve_uefa_competitions,
)


def _datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _write_snapshots(fixtures: list[dict], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "current_fixtures.json"
    csv_path = output_dir / "current_fixtures.csv"
    json_path.write_text(
        json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIXTURE_FIELDS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(fixtures)
    return json_path, csv_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source", choices=("all", "fifa", "uefa"), default="all")
    parser.add_argument("--as-of", type=_datetime, default=None)
    parser.add_argument("--to-date", type=_datetime, default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--fifa-url", default=FIFA_CALENDAR_URL)
    parser.add_argument("--fifa-competition-id", default=FIFA_WORLD_CUP_COMPETITION_ID)
    parser.add_argument("--fifa-season-id", default=FIFA_WORLD_CUP_2026_SEASON_ID)
    parser.add_argument("--fifa-count", type=int, default=500)
    parser.add_argument("--uefa-url", default=UEFA_MATCHES_URL)
    parser.add_argument(
        "--uefa-competition",
        action="append",
        choices=("all", *UEFA_CLUB_COMPETITION_BY_KEY),
        help="verified UEFA competition key; repeatable (default: all)",
    )
    parser.add_argument(
        "--uefa-competition-id",
        action="append",
        choices=tuple(UEFA_CLUB_COMPETITION_BY_ID),
        help="backward-compatible verified UEFA competition ID; repeatable",
    )
    parser.add_argument("--uefa-season-year", default=UEFA_CLUB_2027_SEASON_YEAR)
    parser.add_argument("--uefa-page-size", type=int, default=100)
    args = parser.parse_args(argv)

    as_of = args.as_of or datetime.now(timezone.utc)
    to_date = args.to_date or as_of + timedelta(days=45)
    session = requests.Session()
    # Reproducible public-data fetches must not silently inherit a desktop
    # SOCKS/system proxy that is unavailable inside the project environment.
    session.trust_env = False
    if args.uefa_competition and args.uefa_competition_id:
        parser.error("use either --uefa-competition or --uefa-competition-id, not both")
    if args.uefa_competition_id:
        uefa_competitions = tuple(
            dict.fromkeys(
                UEFA_CLUB_COMPETITION_BY_ID[value]
                for value in args.uefa_competition_id
            )
        )
    else:
        try:
            uefa_competitions = resolve_uefa_competitions(args.uefa_competition)
        except ValueError as exc:
            parser.error(str(exc))
    fixtures: list[dict] = []
    if args.source in ("all", "fifa"):
        fixtures.extend(fetch_fifa_fixtures(
            base_url=args.fifa_url,
            competition_id=args.fifa_competition_id,
            season_id=args.fifa_season_id,
            as_of=as_of,
            to_date=to_date,
            count=args.fifa_count,
            timeout=args.timeout,
            session=session,
        ))
    if args.source in ("all", "uefa"):
        fixtures.extend(fetch_uefa_club_fixtures(
            base_url=args.uefa_url,
            competitions=uefa_competitions,
            season_year=args.uefa_season_year,
            as_of=as_of,
            to_date=to_date,
            page_size=args.uefa_page_size,
            timeout=args.timeout,
            session=session,
        ))

    fixtures.sort(key=lambda row: (row["kickoff_utc"], row["source"], row["id"]))
    json_path, csv_path = _write_snapshots(fixtures, args.output_dir)
    print(f"wrote {len(fixtures)} fixtures to {json_path} and {csv_path}")


if __name__ == "__main__":
    main()
