"""Read-only fixture feeds backed by the official FIFA and UEFA APIs.

The public functions return a small, source-independent list of dictionaries.
No response is cached or written by this module; callers decide where to persist
the normalized snapshot.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

import requests

FIFA_CALENDAR_URL = "https://api.fifa.com/api/v3/calendar/matches"
UEFA_MATCHES_URL = "https://match.uefa.com/v5/matches"

FIFA_WORLD_CUP_COMPETITION_ID = "17"
FIFA_WORLD_CUP_2026_SEASON_ID = "285023"
UEFA_CHAMPIONS_LEAGUE_COMPETITION_ID = "1"
UEFA_CHAMPIONS_LEAGUE_2027_SEASON_YEAR = "2027"

FIXTURE_FIELDS = (
    "source",
    "id",
    "competition_id",
    "competition",
    "season_id",
    "kickoff_utc",
    "home_id",
    "home",
    "away_id",
    "away",
    "venue",
    "round",
    "stage",
    "leg",
    "first_leg_home_score",
    "first_leg_away_score",
    "aggregate_home_score",
    "aggregate_away_score",
    "referee",
)


def _as_utc(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(f"invalid ISO-8601 datetime: {value!r}") from exc
    if not isinstance(value, datetime):
        raise TypeError("datetime value must be an ISO string or datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _kickoff(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _as_utc(value)
    except ValueError:
        return None


def _translation(value: Any, *, keys: Iterable[str] = ("Description",)) -> str | None:
    """Extract English text from either FIFA arrays or UEFA translation maps."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        preferred = sorted(
            (item for item in value if isinstance(item, Mapping)),
            key=lambda item: str(item.get("Locale", "")).lower() not in {
                "en",
                "en-gb",
            },
        )
        for item in preferred:
            for key in keys:
                text = item.get(key)
                if isinstance(text, str) and text.strip():
                    return text.strip()
    if isinstance(value, Mapping):
        for language in ("EN", "en", "en-GB"):
            text = value.get(language)
            if isinstance(text, str) and text.strip():
                return text.strip()
        for text in value.values():
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _nested(mapping: Any, *path: str) -> Any:
    current = mapping
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _fifa_team(
    team: Any, placeholder: Any = None
) -> tuple[str | None, str | None]:
    if not isinstance(team, Mapping):
        text = str(placeholder).strip() if placeholder is not None else ""
        return (f"placeholder:{text}", text) if text else (None, None)
    name = team.get("ShortClubName") or _translation(team.get("TeamName"))
    team_id = str(team.get("IdTeam")) if team.get("IdTeam") is not None else None
    if not name and placeholder is not None:
        name = str(placeholder).strip() or None
        team_id = team_id or (f"placeholder:{name}" if name else None)
    return team_id, name


def _fifa_referee(officials: Any) -> str | None:
    if not isinstance(officials, list):
        return None
    for official in officials:
        if not isinstance(official, Mapping):
            continue
        role = _translation(official.get("TypeLocalized"))
        if official.get("OfficialType") == 1 or (role and role.lower() == "referee"):
            return _translation(official.get("Name")) or _translation(
                official.get("NameShort")
            )
    return None


def _fifa_first_leg(match: Mapping[str, Any]) -> tuple[int | None, int | None]:
    info = match.get("MatchLegInfo")
    containers = [match, info] if isinstance(info, Mapping) else [match]
    home_keys = ("FirstLegHomeTeamScore", "FirstLegHomeScore", "HomeTeamFirstLegScore")
    away_keys = ("FirstLegAwayTeamScore", "FirstLegAwayScore", "AwayTeamFirstLegScore")
    for container in containers:
        home = next((_integer(container.get(key)) for key in home_keys if key in container), None)
        away = next((_integer(container.get(key)) for key in away_keys if key in container), None)
        if home is not None or away is not None:
            return home, away
    return None, None


def normalize_fifa_fixture(match: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize one FIFA calendar object, or return ``None`` if unusable."""
    kickoff = _kickoff(match.get("Date"))
    home_id, home = _fifa_team(match.get("Home"), match.get("PlaceHolderA"))
    away_id, away = _fifa_team(match.get("Away"), match.get("PlaceHolderB"))
    match_id = match.get("IdMatch")
    if kickoff is None or match_id is None or not home or not away:
        return None

    stadium = match.get("Stadium")
    venue = _translation(stadium.get("Name")) if isinstance(stadium, Mapping) else None
    first_home, first_away = _fifa_first_leg(match)
    leg = match.get("Leg")
    if isinstance(leg, Mapping):
        leg = leg.get("Number") or _translation(leg.get("Name"))
    return {
        "source": "fifa",
        "id": str(match_id),
        "competition_id": str(match.get("IdCompetition", "")) or None,
        "competition": _translation(match.get("CompetitionName")),
        "season_id": str(match.get("IdSeason", "")) or None,
        "kickoff_utc": _iso_utc(kickoff),
        "home_id": home_id,
        "home": home,
        "away_id": away_id,
        "away": away,
        "venue": venue,
        "round": _translation(match.get("GroupName")) or (
            f"Match {match['MatchNumber']}" if match.get("MatchNumber") is not None else None
        ),
        "stage": _translation(match.get("StageName")),
        "leg": leg,
        "first_leg_home_score": first_home,
        "first_leg_away_score": first_away,
        "aggregate_home_score": _integer(match.get("AggregateHomeTeamScore")),
        "aggregate_away_score": _integer(match.get("AggregateAwayTeamScore")),
        "referee": _fifa_referee(match.get("Officials")),
    }


def _uefa_team(team: Any) -> tuple[str | None, str | None]:
    if not isinstance(team, Mapping):
        return None, None
    name = team.get("internationalName") or _translation(
        _nested(team, "translations", "displayName")
    )
    return str(team.get("id")) if team.get("id") is not None else None, name


def _uefa_score(match: Mapping[str, Any]) -> tuple[int | None, int | None]:
    for score_type in ("total", "regular"):
        score = _nested(match, "score", score_type)
        if isinstance(score, Mapping):
            home, away = _integer(score.get("home")), _integer(score.get("away"))
            if home is not None and away is not None:
                return home, away
    return None, None


def _uefa_first_leg(
    match: Mapping[str, Any], home_id: str | None, away_id: str | None
) -> tuple[int | None, int | None]:
    related = match.get("relatedMatches")
    if not isinstance(related, list):
        return None, None
    for previous in related:
        if not isinstance(previous, Mapping):
            continue
        kind = str(previous.get("type", "")).upper()
        if kind != "FIRST_LEG":
            continue
        score_home, score_away = _uefa_score(previous)
        previous_home_id, _ = _uefa_team(previous.get("homeTeam"))
        previous_away_id, _ = _uefa_team(previous.get("awayTeam"))
        if previous_home_id == home_id and previous_away_id == away_id:
            return score_home, score_away
        if previous_home_id == away_id and previous_away_id == home_id:
            return score_away, score_home
    return None, None


def _uefa_referee(referees: Any) -> str | None:
    if not isinstance(referees, list):
        return None
    for official in referees:
        if not isinstance(official, Mapping) or official.get("role") != "REFEREE":
            continue
        return _translation(_nested(official, "person", "translations", "name"))
    return None


def normalize_uefa_fixture(match: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize one UEFA match object, including a prior leg when supplied."""
    kickoff = _kickoff(_nested(match, "kickOffTime", "dateTime"))
    home_id, home = _uefa_team(match.get("homeTeam"))
    away_id, away = _uefa_team(match.get("awayTeam"))
    match_id = match.get("id")
    if kickoff is None or match_id is None or not home or not away:
        return None

    first_home, first_away = _uefa_first_leg(match, home_id, away_id)
    aggregate = _nested(match, "score", "aggregate")
    aggregate_home = _integer(aggregate.get("home")) if isinstance(aggregate, Mapping) else None
    aggregate_away = _integer(aggregate.get("away")) if isinstance(aggregate, Mapping) else None
    if aggregate_home is None and aggregate_away is None:
        aggregate_home, aggregate_away = first_home, first_away

    competition = match.get("competition")
    stadium = match.get("stadium")
    round_info = match.get("round")
    leg_info = match.get("leg")
    leg = leg_info.get("number") if isinstance(leg_info, Mapping) else None
    if leg is None:
        kind = match.get("type")
        leg = {"FIRST_LEG": 1, "SECOND_LEG": 2}.get(str(kind).upper())
    return {
        "source": "uefa",
        "id": str(match_id),
        "competition_id": str(competition.get("id")) if isinstance(competition, Mapping) and competition.get("id") is not None else None,
        "competition": (
            competition.get("metaData", {}).get("name")
            if isinstance(competition, Mapping)
            else None
        ) or _translation(_nested(competition, "translations", "name")),
        "season_id": str(match.get("seasonYear", "")) or None,
        "kickoff_utc": _iso_utc(kickoff),
        "home_id": home_id,
        "home": home,
        "away_id": away_id,
        "away": away,
        "venue": _translation(_nested(stadium, "translations", "officialName"))
        or _translation(_nested(stadium, "translations", "name")),
        "round": (
            round_info.get("metaData", {}).get("name")
            if isinstance(round_info, Mapping)
            else None
        ) or _translation(_nested(round_info, "translations", "name")),
        "stage": match.get("competitionPhase") or _nested(match, "matchday", "phase"),
        "leg": _integer(leg),
        "first_leg_home_score": first_home,
        "first_leg_away_score": first_away,
        "aggregate_home_score": aggregate_home,
        "aggregate_away_score": aggregate_away,
        "referee": _uefa_referee(match.get("referees")),
    }


def fetch_fifa_fixtures(
    *,
    base_url: str = FIFA_CALENDAR_URL,
    competition_id: str | int = FIFA_WORLD_CUP_COMPETITION_ID,
    season_id: str | int = FIFA_WORLD_CUP_2026_SEASON_ID,
    as_of: datetime | str | None = None,
    to_date: datetime | str | None = None,
    count: int = 500,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Fetch and normalize future FIFA fixtures without mutating the source."""
    cutoff = _as_utc(as_of)
    end = _as_utc(to_date) if to_date is not None else cutoff + timedelta(days=370)
    if end <= cutoff:
        raise ValueError("to_date must be later than as_of")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count must be a positive integer")

    client = session or requests.Session()
    response = client.get(
        base_url,
        params={
            "idCompetition": str(competition_id),
            "idSeason": str(season_id),
            "language": "en",
            "count": count,
            "from": _iso_utc(cutoff),
            "to": _iso_utc(end),
        },
        headers={"Accept": "application/json", "User-Agent": "xgedge-official-feed/1"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping) or not isinstance(payload.get("Results"), list):
        raise ValueError("unexpected FIFA response: Results array is missing")

    fixtures = []
    for match in payload["Results"]:
        if not isinstance(match, Mapping):
            continue
        normalized = normalize_fifa_fixture(match)
        if normalized is None:
            continue
        kickoff = _as_utc(normalized["kickoff_utc"])
        scores_absent = match.get("HomeTeamScore") is None and match.get("AwayTeamScore") is None
        if cutoff < kickoff <= end and scores_absent:
            fixtures.append(normalized)
    return sorted(fixtures, key=lambda row: (row["kickoff_utc"], row["id"]))


def fetch_uefa_fixtures(
    *,
    base_url: str = UEFA_MATCHES_URL,
    competition_id: str | int = UEFA_CHAMPIONS_LEAGUE_COMPETITION_ID,
    season_year: str | int = UEFA_CHAMPIONS_LEAGUE_2027_SEASON_YEAR,
    as_of: datetime | str | None = None,
    to_date: datetime | str | None = None,
    page_size: int = 100,
    max_pages: int = 50,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Fetch paginated future UEFA fixtures and normalize them."""
    cutoff = _as_utc(as_of)
    end = _as_utc(to_date) if to_date is not None else cutoff + timedelta(days=370)
    if end <= cutoff:
        raise ValueError("to_date must be later than as_of")
    for value, name in ((page_size, "page_size"), (max_pages, "max_pages")):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")

    client = session or requests.Session()
    raw_matches: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    for _ in range(max_pages):
        response = client.get(
            base_url,
            params={
                "competitionId": str(competition_id),
                "seasonYear": str(season_year),
                "fromDate": cutoff.date().isoformat(),
                "toDate": end.date().isoformat(),
                "limit": page_size,
                "offset": offset,
                "order": "ASC",
            },
            headers={"Accept": "application/json", "User-Agent": "xgedge-official-feed/1"},
            timeout=timeout,
        )
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, list):
            raise ValueError("unexpected UEFA response: expected a match array")
        new_count = 0
        for match in page:
            if not isinstance(match, Mapping) or match.get("id") is None:
                continue
            identity = str(match["id"])
            if identity not in seen:
                seen.add(identity)
                raw_matches.append(match)
                new_count += 1
        if len(page) < page_size or new_count == 0:
            break
        offset += len(page)

    fixtures = []
    excluded_statuses = {"FINISHED", "LIVE", "PLAYING", "CANCELLED", "ABANDONED"}
    for match in raw_matches:
        normalized = normalize_uefa_fixture(match)
        if normalized is None:
            continue
        kickoff = _as_utc(normalized["kickoff_utc"])
        status = str(match.get("status", "")).upper()
        if cutoff < kickoff <= end and status not in excluded_statuses:
            fixtures.append(normalized)
    return sorted(fixtures, key=lambda row: (row["kickoff_utc"], row["id"]))
