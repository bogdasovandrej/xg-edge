"""Predict future UEFA club qualifiers from official fixtures and ClubElo."""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from xgedge.data.official_feeds import (
    UEFA_CLUB_2027_SEASON_YEAR,
    UEFA_CLUB_COMPETITION_BY_ID,
    UEFA_CLUB_COMPETITION_BY_KEY,
    UEFA_MATCHES_URL,
    fetch_uefa_club_fixtures,
    resolve_uefa_competitions,
)
from xgedge.experiments.ucl_qualifying import (
    CLUBELO_ATTRIBUTION_URL,
    DEFAULT_CLUBELO_URL,
    EloPoissonCalibration,
    add_uefa_elo_fallbacks,
    build_team_goal_environment,
    clubelo_ranking_url,
    coverage_summary,
    fetch_clubelo_ratings,
    parse_clubelo_csv,
    predict_fixtures,
)


def _datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_fixture_json(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("fixtures")
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError("fixture JSON must be a list or an object with a fixtures list")
    return payload


def _load_aliases(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
    ):
        raise ValueError("aliases JSON must be an object of string-to-string mappings")
    return payload


def _flatten(prediction: dict[str, Any]) -> dict[str, Any]:
    row = {
        key: prediction.get(key)
        for key in (
            "fixture_id", "kickoff_utc", "competition_id", "competition",
            "season_id", "round", "stage", "leg", "home", "away", "status",
            "reason",
        )
    }
    probabilities = prediction.get("probabilities_90m") or {}
    expected = prediction.get("expected_goals_90m") or {}
    qualification = prediction.get("qualification") or {}
    ratings = prediction.get("ratings") or {}
    row.update(
        {
            "home_elo": (ratings.get("home") or {}).get("elo"),
            "away_elo": (ratings.get("away") or {}).get("elo"),
            "lambda_home": expected.get("home"),
            "lambda_away": expected.get("away"),
            "p_home_90m": probabilities.get("home_win"),
            "p_draw_90m": probabilities.get("draw"),
            "p_away_90m": probabilities.get("away_win"),
            "p_home_to_advance": qualification.get("home_to_advance"),
            "p_away_to_advance": qualification.get("away_to_advance"),
            "p_extra_time": qualification.get("extra_time"),
            "missing_teams": "|".join(prediction.get("missing_teams", [])),
        }
    )
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live", "offline"), default="live")
    parser.add_argument("--as-of", type=_datetime, default=None)
    parser.add_argument("--to-date", type=_datetime, default=None)
    parser.add_argument("--limit", type=int, default=14)
    parser.add_argument("--fixtures-json", type=Path)
    parser.add_argument("--ratings-csv", type=Path)
    parser.add_argument("--aliases-json", type=Path)
    parser.add_argument(
        "--history-json",
        type=Path,
        help="official UEFA history used only before as-of for match-specific totals",
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--clubelo-url", default=DEFAULT_CLUBELO_URL)
    parser.add_argument("--uefa-url", default=UEFA_MATCHES_URL)
    parser.add_argument(
        "--uefa-competition",
        action="append",
        choices=("all", *UEFA_CLUB_COMPETITION_BY_KEY),
        help="verified UEFA competition key; repeatable (default: ucl)",
    )
    parser.add_argument(
        "--uefa-competition-id",
        action="append",
        choices=tuple(UEFA_CLUB_COMPETITION_BY_ID),
        help="backward-compatible verified UEFA competition ID; repeatable",
    )
    parser.add_argument("--uefa-season-year", default=UEFA_CLUB_2027_SEASON_YEAR)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--simulations", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=20260713)
    args = parser.parse_args(argv)

    if args.limit < 1:
        parser.error("--limit must be positive")
    as_of = args.as_of or datetime.now(timezone.utc)
    to_date = args.to_date or as_of + timedelta(days=370)
    aliases = _load_aliases(args.aliases_json)
    if args.uefa_competition and args.uefa_competition_id:
        parser.error("use either --uefa-competition or --uefa-competition-id, not both")
    if args.uefa_competition_id:
        competitions = tuple(
            dict.fromkeys(
                UEFA_CLUB_COMPETITION_BY_ID[value]
                for value in args.uefa_competition_id
            )
        )
    else:
        try:
            competitions = resolve_uefa_competitions(args.uefa_competition or ("ucl",))
        except ValueError as exc:
            parser.error(str(exc))

    if args.mode == "offline":
        if args.fixtures_json is None or args.ratings_csv is None:
            parser.error("offline mode requires --fixtures-json and --ratings-csv")
        fixtures = _load_fixture_json(args.fixtures_json)
        rating_rows = parse_clubelo_csv(args.ratings_csv.read_text(encoding="utf-8"))
        ratings_url = f"file:{args.ratings_csv.name}"
        fixture_source = f"file:{args.fixtures_json.name}"
    else:
        session = requests.Session()
        session.trust_env = False
        fixtures = fetch_uefa_club_fixtures(
            base_url=args.uefa_url,
            competitions=competitions,
            season_year=args.uefa_season_year,
            as_of=as_of,
            to_date=to_date,
            timeout=args.timeout,
            session=session,
        )
        rating_rows, ratings_url = fetch_clubelo_ratings(
            as_of=as_of,
            url_template=args.clubelo_url,
            timeout=args.timeout,
            session=session,
        )
        fixture_source = args.uefa_url

    fixtures = sorted(fixtures, key=lambda row: (str(row.get("kickoff_utc", "")), str(row.get("id", ""))))[: args.limit]
    calibration = EloPoissonCalibration()
    history_document = (
        json.loads(args.history_json.read_text(encoding="utf-8"))
        if args.history_json and args.history_json.exists()
        else None
    )
    goal_environment = build_team_goal_environment(
        history_document,
        as_of=as_of,
        calibration=calibration,
    )
    rating_rows, rating_coverage = add_uefa_elo_fallbacks(
        fixtures,
        rating_rows,
        history_document,
        as_of=as_of,
        aliases=aliases,
    )
    predictions = predict_fixtures(
        fixtures,
        rating_rows,
        as_of=as_of,
        aliases=aliases,
        calibration=calibration,
        goal_environment=goal_environment,
        simulations=args.simulations,
        seed=args.seed,
    )
    envelope = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "as_of_utc": as_of.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "model": "experimental hybrid Elo-to-Poisson UEFA qualifier baseline",
        "calibration": asdict(calibration),
        "coverage": coverage_summary(predictions),
        "sources": {
            "fixtures": {
                "provider": "UEFA",
                "url": fixture_source,
                "competitions": [
                    {
                        "key": competition.key,
                        "id": competition.competition_id,
                        "code": competition.code,
                        "name": competition.name,
                    }
                    for competition in competitions
                ],
            },
            "ratings": {
                "provider": "ClubElo + xgedge UEFA Elo fallback",
                "url": ratings_url,
                "documentation": CLUBELO_ATTRIBUTION_URL,
                "attribution": (
                    "ClubElo where matched; otherwise point-in-time Elo replayed "
                    "from official UEFA regulation-time results."
                ),
                "fixture_team_coverage": rating_coverage,
                "fallback_method": "xgedge_point_in_time_uefa_elo_v1",
            },
            "goal_environment": {
                "provider": "UEFA",
                "history_file": (
                    args.history_json.name if args.history_json else None
                ),
                "teams_with_pre_match_history": len(goal_environment),
                "method": "recent official 90-minute totals with Bayesian shrinkage",
            },
        },
        "limitations": [
            "Experimental baseline; no demonstrated betting or CLV edge.",
            "90-minute strength split uses ClubElo with a point-in-time official UEFA Elo fallback.",
            "Lineups, injuries and odds are not inputs to this experimental goal model.",
            "Advancement simulation is separate and is emitted only for a second leg with a known aggregate.",
            "A team without ClubElo or prior UEFA results uses a neutral 1500 cold-start with wider uncertainty.",
        ],
        "predictions": predictions,
    }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rows = [_flatten(prediction) for prediction in predictions]
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["fixture_id"])
        writer.writeheader()
        writer.writerows(rows)
    summary = envelope["coverage"]
    print(
        f"predicted {summary['predicted']}/{summary['fixtures']} UEFA fixtures "
        f"({summary['coverage']:.1%}); no-prediction={summary['no_prediction']}"
    )
    print(f"ClubElo snapshot: {ratings_url}")
    print(f"JSON: {args.output_json}")
    print(f"CSV: {args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
