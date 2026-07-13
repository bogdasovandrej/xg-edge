"""Read-only adapter for the official StatsBomb Open Data repository.

StatsBomb Open Data is useful for historical research and model calibration.  It
is not a live feed and this module deliberately makes no claim that it covers a
current competition or season.  Users publishing analysis based on the data
must retain the StatsBomb attribution included in every generated snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Iterable, Mapping

import requests

STATSBOMB_OPEN_DATA_BASE = (
    "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
)
STATSBOMB_OPEN_DATA_REPOSITORY = "https://github.com/statsbomb/open-data"
STATSBOMB_ATTRIBUTION = "Data provided by StatsBomb. Visit https://statsbomb.com."
USAGE_MODE = "historical_calibration_only"
SCHEMA_VERSION = "statsbomb-open/1.0"
PUBLIC_USER_AGENT = "xgedge/0.5 (+https://github.com/bogdasovandrej/xg-edge)"


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and parsed >= 0 else None


def _name(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        text = value.get("name")
        return text.strip() if isinstance(text, str) and text.strip() else None
    return None


def _entity(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    identifier = _integer(value.get("id"))
    name = _name(value)
    if identifier is None and name is None:
        return None
    return {"id": identifier, "name": name}


def _country(value: Any) -> dict[str, Any] | None:
    return _entity(value)


def _identifier(value: int | str, *, field: str) -> str:
    text = str(value).strip()
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return text


def _iso_utc(value: datetime | None = None) -> str:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def source_provenance(
    *, source_urls: Iterable[str], fetched_at: datetime | None = None
) -> dict[str, Any]:
    """Return the attribution and limitations that must travel with the data."""
    return {
        "source": "statsbomb_open_data",
        "provider": "StatsBomb",
        "repository": STATSBOMB_OPEN_DATA_REPOSITORY,
        "source_urls": list(dict.fromkeys(str(url) for url in source_urls)),
        "fetched_at": _iso_utc(fetched_at),
        "attribution": STATSBOMB_ATTRIBUTION,
        "usage_mode": USAGE_MODE,
        "current_coverage_guaranteed": False,
    }


def normalize_competition(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one row from ``competitions.json`` without inventing coverage."""
    return {
        "source": "statsbomb_open_data",
        "competition_id": _integer(row.get("competition_id")),
        "season_id": _integer(row.get("season_id")),
        "country": _name(row.get("country_name")),
        "competition": _name(row.get("competition_name")),
        "season": _name(row.get("season_name")),
        "gender": _name(row.get("competition_gender")),
        "is_youth": row.get("competition_youth")
        if isinstance(row.get("competition_youth"), bool)
        else None,
        "is_international": row.get("competition_international")
        if isinstance(row.get("competition_international"), bool)
        else None,
        "match_updated": row.get("match_updated"),
        "match_available": row.get("match_available"),
        "match_updated_360": row.get("match_updated_360"),
        "match_available_360": row.get("match_available_360"),
        "usage_mode": USAGE_MODE,
        "current_coverage_guaranteed": False,
    }


def _team(value: Any, *, side: str) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    identifier = _integer(value.get(f"{side}_team_id"))
    name = _name(value.get(f"{side}_team_name"))
    if identifier is None and name is None:
        return None
    return {
        "id": identifier,
        "name": name,
        "gender": _name(value.get(f"{side}_team_gender")),
        "group": _name(value.get(f"{side}_team_group")),
        "country": _country(value.get("country")),
    }


def _match_competition(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    identifier = _integer(value.get("competition_id"))
    name = _name(value.get("competition_name"))
    if identifier is None and name is None:
        return None
    return {
        "id": identifier,
        "name": name,
        "country": _name(value.get("country_name")),
    }


def _match_season(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    identifier = _integer(value.get("season_id"))
    name = _name(value.get("season_name"))
    if identifier is None and name is None:
        return None
    return {"id": identifier, "name": name}


def normalize_match(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize historical match metadata, including referee identity."""
    competition = _match_competition(row.get("competition"))
    season = _match_season(row.get("season"))
    stage = _entity(row.get("competition_stage"))
    stadium = _entity(row.get("stadium"))
    if stadium is not None and isinstance(row.get("stadium"), Mapping):
        stadium["country"] = _country(row["stadium"].get("country"))
    referee = _entity(row.get("referee"))
    if referee is not None and isinstance(row.get("referee"), Mapping):
        referee["country"] = _country(row["referee"].get("country"))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    return {
        "source": "statsbomb_open_data",
        "match_id": _integer(row.get("match_id")),
        "match_date": row.get("match_date"),
        # StatsBomb's field is local wall-clock time; do not mislabel it as UTC.
        "kickoff_local": row.get("kick_off"),
        "competition": competition,
        "season": season,
        "stage": stage,
        "match_week": _integer(row.get("match_week")),
        "home_team": _team(row.get("home_team"), side="home"),
        "away_team": _team(row.get("away_team"), side="away"),
        "score": {
            "home": _integer(row.get("home_score")),
            "away": _integer(row.get("away_score")),
        },
        "stadium": stadium,
        "referee": referee,
        "metadata": {
            "data_version": metadata.get("data_version"),
            "shot_fidelity_version": metadata.get("shot_fidelity_version"),
            "xy_fidelity_version": metadata.get("xy_fidelity_version"),
        },
        "last_updated": row.get("last_updated"),
        "last_updated_360": row.get("last_updated_360"),
        "usage_mode": USAGE_MODE,
    }


def _event_order(indexed: tuple[int, Mapping[str, Any]]) -> tuple[int, int, int, str, int]:
    index, event = indexed
    return (
        _integer(event.get("period")) or 0,
        _integer(event.get("minute")) or 0,
        _integer(event.get("second")) or 0,
        str(event.get("timestamp") or ""),
        index,
    )


def _red_card(event: Mapping[str, Any]) -> str | None:
    for container_name in ("foul_committed", "bad_behaviour"):
        container = event.get(container_name)
        if not isinstance(container, Mapping):
            continue
        card = _name(container.get("card"))
        if card and card.casefold() in {
            "red card",
            "second yellow",
            "second yellow card",
        }:
            return card
    return None


def _side(team_id: int | None, *, home_team_id: int, away_team_id: int) -> str | None:
    if team_id == home_team_id:
        return "home"
    if team_id == away_team_id:
        return "away"
    return None


def normalize_events(
    events: Iterable[Mapping[str, Any]],
    *,
    home_team_id: int,
    away_team_id: int,
) -> dict[str, Any]:
    """Aggregate shot xG and retain event-level dismissals.

    ``score_before`` is reconstructed in event order from goals in the same
    event file.  It therefore describes the state immediately before a red-card
    event, which is the safe input for red-card xG adjustments.
    """
    home_id = int(_identifier(home_team_id, field="home_team_id"))
    away_id = int(_identifier(away_team_id, field="away_team_id"))
    if home_id == away_id:
        raise ValueError("home_team_id and away_team_id must differ")
    indexed = [
        (index, event)
        for index, event in enumerate(events)
        if isinstance(event, Mapping)
    ]
    indexed.sort(key=_event_order)
    metrics: dict[str, dict[str, Any]] = {
        side: {
            "team_id": identifier,
            "shots": 0,
            "shots_with_xg": 0,
            "xg": 0.0,
            "npxg": 0.0,
            "penalties": {"taken": 0, "scored": 0, "xg": 0.0},
        }
        for side, identifier in (("home", home_id), ("away", away_id))
    }
    score = {"home": 0, "away": 0}
    red_cards: list[dict[str, Any]] = []
    shootout_events_excluded = 0
    own_goal_for_signatures = {
        (
            _integer(event.get("period")),
            _integer(event.get("minute")),
            _integer(event.get("second")),
            str(event.get("timestamp") or ""),
        )
        for _, event in indexed
        if _name(event.get("type")) == "Own Goal For"
    }

    for _, event in indexed:
        event_type = _name(event.get("type"))
        team = _entity(event.get("team"))
        team_id = team["id"] if team else None
        side = _side(team_id, home_team_id=home_id, away_team_id=away_id)
        card = _red_card(event)
        if card:
            red_cards.append({
                "event_id": str(event.get("id")) if event.get("id") is not None else None,
                "period": _integer(event.get("period")),
                "minute": _integer(event.get("minute")),
                "second": _integer(event.get("second")),
                "timestamp": event.get("timestamp"),
                "team": team,
                "side": side,
                "card": card,
                "event_type": event_type,
                "score_before": dict(score),
            })

        period = _integer(event.get("period"))
        if event_type == "Shot" and period == 5:
            # Shootout attempts are not match chances and must not inflate xG.
            shootout_events_excluded += 1
            continue
        if event_type == "Shot" and side:
            shot = event.get("shot") if isinstance(event.get("shot"), Mapping) else {}
            xg = _number(shot.get("statsbomb_xg"))
            is_penalty = (_name(shot.get("type")) or "").casefold() == "penalty"
            is_goal = (_name(shot.get("outcome")) or "").casefold() == "goal"
            metrics[side]["shots"] += 1
            if xg is not None:
                metrics[side]["shots_with_xg"] += 1
                metrics[side]["xg"] += xg
                if not is_penalty:
                    metrics[side]["npxg"] += xg
            if is_penalty:
                metrics[side]["penalties"]["taken"] += 1
                metrics[side]["penalties"]["xg"] += xg or 0.0
                if is_goal:
                    metrics[side]["penalties"]["scored"] += 1
            if is_goal:
                score[side] += 1
        elif event_type == "Own Goal For" and side:
            score[side] += 1
        elif event_type == "Own Goal Against" and side:
            signature = (
                _integer(event.get("period")),
                _integer(event.get("minute")),
                _integer(event.get("second")),
                str(event.get("timestamp") or ""),
            )
            # StatsBomb normally emits a paired Own Goal For/Against event.
            # Count the Against event only when its For counterpart is absent.
            if signature not in own_goal_for_signatures:
                score["away" if side == "home" else "home"] += 1

    for values in metrics.values():
        values["xg"] = round(values["xg"], 6)
        values["npxg"] = round(values["npxg"], 6)
        values["penalties"]["xg"] = round(values["penalties"]["xg"], 6)
    return {
        "event_count": len(indexed),
        "home": metrics["home"],
        "away": metrics["away"],
        "goals_from_events": score,
        "red_cards": red_cards,
        "red_card_count": len(red_cards),
        "shootout_events_excluded": shootout_events_excluded,
    }


def _normalize_card(card: Any) -> dict[str, Any] | None:
    if not isinstance(card, Mapping):
        return None
    return {
        "time": card.get("time"),
        "card_type": card.get("card_type"),
        "reason": card.get("reason"),
        "period": _integer(card.get("period")),
    }


def _normalize_position(position: Any) -> dict[str, Any] | None:
    if not isinstance(position, Mapping):
        return None
    return {
        "position_id": _integer(position.get("position_id")),
        "position": position.get("position"),
        "from": position.get("from"),
        "to": position.get("to"),
        "from_period": _integer(position.get("from_period")),
        "to_period": _integer(position.get("to_period")),
        "start_reason": position.get("start_reason"),
        "end_reason": position.get("end_reason"),
    }


def normalize_lineups(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Normalize teams, players, cards and positional intervals."""
    teams: list[dict[str, Any]] = []
    player_count = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        players = []
        raw_lineup = row.get("lineup") if isinstance(row.get("lineup"), list) else []
        for raw in raw_lineup:
            if not isinstance(raw, Mapping):
                continue
            cards = [item for item in (_normalize_card(card) for card in raw.get("cards", [])) if item]
            positions = [
                item
                for item in (
                    _normalize_position(position) for position in raw.get("positions", [])
                )
                if item
            ]
            players.append({
                "player_id": _integer(raw.get("player_id")),
                "player_name": raw.get("player_name"),
                "player_nickname": raw.get("player_nickname"),
                "jersey_number": _integer(raw.get("jersey_number")),
                "country": _country(raw.get("country")),
                "cards": cards,
                "positions": positions,
            })
        player_count += len(players)
        teams.append({
            "team_id": _integer(row.get("team_id")),
            "team_name": row.get("team_name"),
            "players": players,
        })
    return {"team_count": len(teams), "player_count": player_count, "teams": teams}


def build_match_record(
    match: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
    lineups: Iterable[Mapping[str, Any]],
    *,
    source_urls: Iterable[str] = (),
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    """Build one compact calibration record; raw event streams are not copied."""
    normalized_match = normalize_match(match)
    home = normalized_match.get("home_team") or {}
    away = normalized_match.get("away_team") or {}
    if home.get("id") is None or away.get("id") is None:
        raise ValueError("match must contain numeric home and away team ids")
    return {
        "schema_version": SCHEMA_VERSION,
        "usage_mode": USAGE_MODE,
        "current_coverage_guaranteed": False,
        "match": normalized_match,
        "events": normalize_events(
            events,
            home_team_id=home["id"],
            away_team_id=away["id"],
        ),
        "lineups": normalize_lineups(lineups),
        "provenance": source_provenance(
            source_urls=source_urls, fetched_at=fetched_at
        ),
    }


def _url(base_url: str, resource: str) -> str:
    return f"{base_url.rstrip('/')}/{resource.lstrip('/')}"


def _fetch_json(
    url: str,
    *,
    expected: type,
    timeout: float,
    session: requests.Session | None,
) -> Any:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    client = session or requests.Session()
    response = client.get(url, timeout=timeout, headers={"User-Agent": PUBLIC_USER_AGENT})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, expected):
        raise ValueError(f"unexpected StatsBomb payload at {url}")
    return payload


def fetch_catalog(
    *,
    base_url: str = STATSBOMB_OPEN_DATA_BASE,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    url = _url(base_url, "competitions.json")
    payload = _fetch_json(url, expected=list, timeout=timeout, session=session)
    return [normalize_competition(row) for row in payload if isinstance(row, Mapping)]


def fetch_matches(
    competition_id: int | str,
    season_id: int | str,
    *,
    base_url: str = STATSBOMB_OPEN_DATA_BASE,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    competition = _identifier(competition_id, field="competition_id")
    season = _identifier(season_id, field="season_id")
    url = _url(base_url, f"matches/{competition}/{season}.json")
    payload = _fetch_json(url, expected=list, timeout=timeout, session=session)
    return [normalize_match(row) for row in payload if isinstance(row, Mapping)]


def fetch_match_record(
    competition_id: int | str,
    season_id: int | str,
    match_id: int | str,
    *,
    base_url: str = STATSBOMB_OPEN_DATA_BASE,
    timeout: float = 30.0,
    session: requests.Session | None = None,
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    """Download events/lineups for exactly one historical match."""
    competition = _identifier(competition_id, field="competition_id")
    season = _identifier(season_id, field="season_id")
    match = _identifier(match_id, field="match_id")
    urls = {
        "matches": _url(base_url, f"matches/{competition}/{season}.json"),
        "events": _url(base_url, f"events/{match}.json"),
        "lineups": _url(base_url, f"lineups/{match}.json"),
    }
    matches = _fetch_json(
        urls["matches"], expected=list, timeout=timeout, session=session
    )
    raw_match = next(
        (
            row
            for row in matches
            if isinstance(row, Mapping) and _integer(row.get("match_id")) == int(match)
        ),
        None,
    )
    if raw_match is None:
        raise LookupError(
            f"match {match} is not present in competition {competition}, season {season}"
        )
    events = _fetch_json(urls["events"], expected=list, timeout=timeout, session=session)
    lineups = _fetch_json(urls["lineups"], expected=list, timeout=timeout, session=session)
    return build_match_record(
        raw_match,
        events,
        lineups,
        source_urls=urls.values(),
        fetched_at=fetched_at,
    )
