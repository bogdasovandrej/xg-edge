"""Official FIFA inputs for the experimental 2026 World Cup model.

The parsers accept both untouched FIFA API responses and the compact normalized
JSON produced by :func:`load_fifa_fixtures` / :func:`load_fifa_rankings`.  This
keeps network access out of tests and makes a saved source snapshot replayable.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import requests

FIFA_RANKINGS_URL = "https://api.fifa.com/api/v3/rankings"
FIFA_WORLD_CUP_CALENDAR_URL = "https://api.fifa.com/api/v3/calendar/matches"
FIFA_TIMELINE_URL = "https://api.fifa.com/api/v3/timelines/{match_id}"
FIFA_WORLD_CUP_COMPETITION_ID = "17"
FIFA_WORLD_CUP_2026_SEASON_ID = "285023"
WORLD_CUP_FIRST_KICKOFF_UTC = "2026-06-11T19:00:00Z"
PUBLIC_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 xgedge/0.3"
)


def parse_utc(value: str | datetime) -> datetime:
    """Parse an aware timestamp and return UTC."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be an ISO-8601 string or datetime")
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def iso_utc(value: str | datetime) -> str:
    return parse_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _english(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, list):
        return None
    rows = [row for row in value if isinstance(row, Mapping)]
    rows.sort(key=lambda row: str(row.get("Locale", "")).lower() not in {"en", "en-gb"})
    for row in rows:
        text = row.get("Description")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _team(raw: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(raw, Mapping):
        return None, None, None
    identity = raw.get("IdTeam")
    name = raw.get("ShortClubName") or _english(raw.get("TeamName"))
    code = raw.get("IdCountry") or raw.get("Abbreviation")
    return (
        str(identity) if identity is not None else None,
        str(name).strip() if name else None,
        str(code).strip() if code else None,
    )


def _read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _get(client: Any, url: str, **kwargs: Any) -> Any:
    """GET with a narrow fallback for an unusable optional SOCKS environment.

    Some Windows Python installations inherit a SOCKS proxy while ``requests``
    was installed without its optional PySocks extra.  In that exact case no
    request has been sent, so retrying directly keeps the live CLI usable.
    """
    try:
        return client.get(url, **kwargs)
    except requests.exceptions.InvalidSchema as exc:
        if "SOCKS" not in str(exc):
            raise
        direct = requests.Session()
        direct.trust_env = False
        return direct.get(url, **kwargs)


def normalize_rankings(
    payload: Any,
    *,
    tournament_start: str | datetime = WORLD_CUP_FIRST_KICKOFF_UTC,
) -> dict[str, Any]:
    """Normalize and validate a pre-tournament men's FIFA ranking snapshot."""
    if isinstance(payload, Mapping) and isinstance(payload.get("rankings"), list):
        raw_rows = payload["rankings"]
        declared_publication = payload.get("publication_utc")
    elif isinstance(payload, Mapping) and isinstance(payload.get("Results"), list):
        raw_rows = payload["Results"]
        declared_publication = None
    elif isinstance(payload, list):
        raw_rows = payload
        declared_publication = None
    else:
        raise ValueError("unexpected FIFA rankings JSON")

    rows: list[dict[str, Any]] = []
    publication_dates: set[str] = set()
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            continue
        normalized = "team_id" in raw and "rating" in raw
        team_id = raw.get("team_id") if normalized else raw.get("IdTeam")
        team = raw.get("team") if normalized else _english(raw.get("TeamName"))
        country_code = raw.get("country_code") if normalized else raw.get("IdCountry")
        rating = raw.get("rating") if normalized else raw.get("DecimalTotalPoints")
        if rating is None and not normalized:
            rating = raw.get("TotalPoints")
        rank = raw.get("rank") if normalized else raw.get("Rank")
        pub = raw.get("publication_utc") if normalized else raw.get("PubDate")
        pub = pub or declared_publication
        if team_id is None or not team or rating is None or pub is None:
            continue
        publication_utc = iso_utc(pub)
        publication_dates.add(publication_utc)
        rows.append(
            {
                "team_id": str(team_id),
                "team": str(team),
                "country_code": str(country_code) if country_code else None,
                "rank": int(rank) if rank is not None else None,
                "rating": float(rating),
            }
        )
    if not rows:
        raise ValueError("FIFA rankings snapshot contains no usable teams")
    if len(publication_dates) != 1:
        raise ValueError("FIFA rankings must come from one publication")
    publication_utc = next(iter(publication_dates))
    if parse_utc(publication_utc) > parse_utc(tournament_start):
        raise ValueError("ranking publication is after the tournament started")
    if len({row["team_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate team ids in FIFA rankings")
    return {
        "source": FIFA_RANKINGS_URL,
        "publication_utc": publication_utc,
        "tournament_start_utc": iso_utc(tournament_start),
        "rankings": sorted(rows, key=lambda row: (row["rank"] or 10_000, row["team_id"])),
    }


def _timeline_regulation_score(payload: Any) -> tuple[int, int]:
    if not isinstance(payload, Mapping):
        raise ValueError("unexpected FIFA timeline JSON")
    events = payload.get("Event")
    if not isinstance(events, list):
        raise ValueError("FIFA timeline has no Event array")
    candidates: list[tuple[int, str, int, int, int]] = []
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        # FIFA periods 3 and 5 are the first and second regulation halves.
        if event.get("Period") not in (3, 5):
            continue
        home, away = event.get("HomeGoals"), event.get("AwayGoals")
        if isinstance(home, int) and isinstance(away, int) and home >= 0 and away >= 0:
            candidates.append(
                (int(event["Period"]), str(event.get("Timestamp", "")), index, home, away)
            )
    latest = max(candidates) if candidates else None
    score = (latest[3], latest[4]) if latest else None
    if score is None:
        # The first extra-time/penalty event still carries the 90-minute score.
        for event in events:
            if not isinstance(event, Mapping) or event.get("Period") not in (7, 9, 11):
                continue
            home, away = event.get("HomeGoals"), event.get("AwayGoals")
            if isinstance(home, int) and isinstance(away, int):
                score = home, away
                break
    if score is None:
        raise ValueError("could not recover the 90-minute score from FIFA timeline")
    return score


def normalize_fixtures(payload: Any) -> dict[str, Any]:
    """Normalize calendar JSON; ET/penalty games require included timelines."""
    timelines: Mapping[str, Any] = {}
    if isinstance(payload, Mapping) and isinstance(payload.get("matches"), list):
        raw_rows = payload["matches"]
    elif isinstance(payload, Mapping) and isinstance(payload.get("calendar"), Mapping):
        raw_rows = payload["calendar"].get("Results")
        timelines = payload.get("timelines", {})
    elif isinstance(payload, Mapping) and isinstance(payload.get("Results"), list):
        raw_rows = payload["Results"]
        timelines = payload.get("timelines", {})
    elif isinstance(payload, list):
        raw_rows = payload
    else:
        raise ValueError("unexpected FIFA fixtures JSON")
    if not isinstance(raw_rows, list):
        raise ValueError("FIFA calendar has no Results array")

    matches: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            continue
        if "kickoff_utc" in raw:
            row = dict(raw)
            required = {"id", "kickoff_utc", "home_id", "home", "away_id", "away", "status"}
            if not required.issubset(row):
                continue
            row["id"] = str(row["id"])
            row["kickoff_utc"] = iso_utc(row["kickoff_utc"])
            if row["status"] == "FINISHED":
                for key in ("home_goals_90", "away_goals_90"):
                    if not isinstance(row.get(key), int) or row[key] < 0:
                        raise ValueError(f"finished match {row['id']} lacks {key}")
            matches.append(row)
            continue

        identity = raw.get("IdMatch")
        kickoff = raw.get("Date")
        home_id, home, home_code = _team(raw.get("Home"))
        away_id, away, away_code = _team(raw.get("Away"))
        if identity is None or not kickoff or not home_id or not away_id or not home or not away:
            continue
        finished = raw.get("MatchStatus") == 0
        row = {
            "id": str(identity),
            "kickoff_utc": iso_utc(kickoff),
            "home_id": home_id,
            "home": home,
            "home_code": home_code,
            "away_id": away_id,
            "away": away,
            "away_code": away_code,
            "stage": _english(raw.get("StageName")),
            "status": "FINISHED" if finished else "SCHEDULED",
        }
        if finished:
            result_type = int(raw.get("ResultType") or 0)
            if result_type == 1:
                home_goals = raw.get("HomeTeamScore")
                away_goals = raw.get("AwayTeamScore")
            else:
                home_goals = raw.get("RegulationHomeTeamScore")
                away_goals = raw.get("RegulationAwayTeamScore")
                timeline = timelines.get(str(identity)) if isinstance(timelines, Mapping) else None
                if (home_goals is None or away_goals is None) and timeline is not None:
                    home_goals, away_goals = _timeline_regulation_score(timeline)
            if not isinstance(home_goals, int) or not isinstance(away_goals, int):
                raise ValueError(
                    f"finished ET/penalty match {identity} needs its FIFA timeline "
                    "to recover the 90-minute score"
                )
            row["home_goals_90"] = home_goals
            row["away_goals_90"] = away_goals
        matches.append(row)
    if not matches:
        raise ValueError("FIFA calendar contains no usable matches")
    if len({row["id"] for row in matches}) != len(matches):
        raise ValueError("duplicate match ids in FIFA calendar")
    return {
        "source": FIFA_WORLD_CUP_CALENDAR_URL,
        "competition_id": FIFA_WORLD_CUP_COMPETITION_ID,
        "season_id": FIFA_WORLD_CUP_2026_SEASON_ID,
        "matches": sorted(matches, key=lambda row: (row["kickoff_utc"], row["id"])),
    }


def load_fifa_rankings(
    path: str | Path | None = None,
    *,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Load a ranking snapshot from JSON or fetch the official live endpoint."""
    if path is not None:
        return normalize_rankings(_read_json(path))
    client = session or requests.Session()
    response = _get(
        client,
        FIFA_RANKINGS_URL,
        params={"gender": 1, "language": "en"},
        headers={"Accept": "application/json", "User-Agent": PUBLIC_USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    return normalize_rankings(response.json())


def load_fifa_fixtures(
    path: str | Path | None = None,
    *,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Load fixtures from JSON or fetch calendar plus required timelines live."""
    if path is not None:
        return normalize_fixtures(_read_json(path))
    client = session or requests.Session()
    response = _get(
        client,
        FIFA_WORLD_CUP_CALENDAR_URL,
        params={
            "idCompetition": FIFA_WORLD_CUP_COMPETITION_ID,
            "idSeason": FIFA_WORLD_CUP_2026_SEASON_ID,
            "language": "en",
            "count": 500,
        },
        headers={"Accept": "application/json", "User-Agent": PUBLIC_USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    calendar = response.json()
    if not isinstance(calendar, Mapping) or not isinstance(calendar.get("Results"), list):
        raise ValueError("unexpected FIFA calendar JSON")
    timelines: dict[str, Any] = {}
    for raw in calendar["Results"]:
        if not isinstance(raw, Mapping) or raw.get("MatchStatus") != 0:
            continue
        if int(raw.get("ResultType") or 0) == 1:
            continue
        identity = str(raw.get("IdMatch"))
        timeline_response = _get(
            client,
            FIFA_TIMELINE_URL.format(match_id=identity),
            headers={"Accept": "application/json", "User-Agent": PUBLIC_USER_AGENT},
            timeout=timeout,
        )
        timeline_response.raise_for_status()
        timelines[identity] = timeline_response.json()
    return normalize_fixtures({"calendar": calendar, "timelines": timelines})
