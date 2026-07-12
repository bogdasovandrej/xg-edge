"""Official UEFA line-up and event snapshots.

Only the public match endpoints are used.  Normalizers are intentionally
tolerant of additive UEFA response changes but fail closed on missing arrays.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Iterable, Mapping

import requests

from xgedge.data.point_in_time import available_snapshot, iso_utc

UEFA_MATCH_URL = "https://match.uefa.com/v5/matches/{match_id}"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _translated(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Mapping):
        for key in ("EN", "en", "en-GB"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
        for text in value.values():
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _identity(entity: Any) -> tuple[str | None, str | None]:
    item = _mapping(entity)
    identifier = item.get("id") or item.get("personId") or item.get("playerId")
    name = item.get("internationalName") or item.get("displayName") or item.get("name")
    if isinstance(name, Mapping):
        name = _translated(name)
    if not name:
        translations = _mapping(item.get("translations"))
        name = _translated(translations.get("name") or translations.get("displayName"))
    return (str(identifier) if identifier is not None else None, str(name) if name else None)


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and parsed >= 0 else None


def _first_present(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _timestamp(payload: Mapping[str, Any], fallback: datetime | str) -> str:
    for key in ("announcedAt", "lastUpdatedAt", "updatedAt", "generatedAt"):
        value = payload.get(key)
        if value:
            try:
                return iso_utc(value, field=key)
            except (TypeError, ValueError):
                continue
    return iso_utc(fallback, field="snapshot_at")


def _team_blocks(payload: Mapping[str, Any]) -> Iterable[tuple[str | None, Mapping[str, Any]]]:
    for side, key in (("home", "homeTeam"), ("away", "awayTeam")):
        block = payload.get(key)
        if isinstance(block, Mapping):
            yield side, block
    lineups = payload.get("lineups")
    if isinstance(lineups, list):
        for block in lineups:
            if isinstance(block, Mapping):
                side = str(block.get("side", "")).lower() or None
                yield side, block


def normalize_uefa_lineups(
    payload: Mapping[str, Any],
    *,
    match_id: str | int,
    snapshot_at: datetime | str,
) -> list[dict[str, Any]]:
    """Normalize confirmed starters/substitutes without inventing minutes."""
    if not isinstance(payload, Mapping):
        raise ValueError("unexpected UEFA lineups response")
    announced_at = _timestamp(payload, snapshot_at)
    output: list[dict[str, Any]] = []
    saw_block = False
    for side, block in _team_blocks(payload):
        saw_block = True
        team_obj = block.get("team") if isinstance(block.get("team"), Mapping) else block
        team_id, team_name = _identity(team_obj)
        groups = (
            ("starter", block.get("players") or block.get("startingXI") or block.get("starters")),
            ("substitute", block.get("substitutes") or block.get("bench")),
        )
        seen_players: set[tuple[str | None, str | None]] = set()
        for default_status, players in groups:
            if not isinstance(players, list):
                continue
            for assignment in players:
                if not isinstance(assignment, Mapping):
                    continue
                player_obj = assignment.get("player") or assignment.get("person") or assignment
                player_id, player_name = _identity(player_obj)
                key = (player_id, player_name)
                if key in seen_players or (player_id is None and player_name is None):
                    continue
                seen_players.add(key)
                raw_status = str(
                    assignment.get("lineupStatus")
                    or assignment.get("status")
                    or default_status
                ).lower()
                status = (
                    "starter"
                    if raw_status in {"starter", "starting", "start", "starting_xi"}
                    else "substitute"
                )
                output.append({
                    "provider": "uefa",
                    "match_id": str(match_id),
                    "snapshot_at": iso_utc(snapshot_at, field="snapshot_at"),
                    "announced_at": announced_at,
                    "team_id": team_id,
                    "team_name": team_name,
                    "side": side,
                    "player_id": player_id,
                    "player_name": player_name,
                    "lineup_status": status,
                    "is_confirmed": True,
                    "expected_minutes": _float(
                        _first_present(
                            assignment.get("expectedMinutes"),
                            _mapping(player_obj).get("expectedMinutes"),
                        )
                    ),
                    "confirmed_minutes": _float(
                        _first_present(
                            assignment.get("minutesPlayed"),
                            assignment.get("confirmedMinutes"),
                            _mapping(player_obj).get("minutesPlayed"),
                        )
                    ),
                })
    if not saw_block:
        raise ValueError("unexpected UEFA lineups response: team lineups are missing")
    return output


def _event_list(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("events", "matchEvents", "items"):
        events = payload.get(key)
        if isinstance(events, list):
            return [event for event in events if isinstance(event, Mapping)]
    raise ValueError("unexpected UEFA events response: events array is missing")


def _minute(event: Mapping[str, Any]) -> tuple[int | None, int | None]:
    clock = _mapping(event.get("time"))
    raw = event.get("minute", clock.get("minute"))
    added = event.get("injuryTime", event.get("addedTime", clock.get("additional")))
    try:
        minute = int(raw) if raw is not None else None
        extra = int(added) if added is not None else 0
    except (TypeError, ValueError):
        return None, None
    return minute, max(extra, 0)


def _event_kind(event: Mapping[str, Any]) -> str:
    return " ".join(
        str(event.get(key, "")) for key in ("type", "subType", "eventType", "cardType")
    ).upper()


def _event_team(event: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    team = event.get("team")
    team_id, team_name = _identity(team)
    if team_id is None and event.get("teamId") is not None:
        team_id = str(event["teamId"])
    side = str(event.get("side", _mapping(team).get("side", ""))).lower() or None
    if side not in {"home", "away"}:
        side = None
    return team_id, team_name, side


def normalize_uefa_events(
    payload: Mapping[str, Any],
    *,
    match_id: str | int,
    snapshot_at: datetime | str,
) -> dict[str, list[dict[str, Any]]]:
    """Normalize event-level red cards, score-before state and referee."""
    if not isinstance(payload, Mapping):
        raise ValueError("unexpected UEFA events response")
    announced_at = _timestamp(payload, snapshot_at)
    events = _event_list(payload)
    def sort_key(item: tuple[int, Mapping[str, Any]]) -> tuple[int, int, int]:
        minute, added = _minute(item[1])
        return (
            minute if minute is not None else 10_000,
            added if added is not None else 0,
            item[0],
        )

    ordered = sorted(enumerate(events), key=sort_key)
    home_score = away_score = 0
    red_cards: list[dict[str, Any]] = []
    for _, event in ordered:
        kind = _event_kind(event)
        minute, added = _minute(event)
        team_id, team_name, side = _event_team(event)
        before = _mapping(event.get("scoreBefore"))
        before_home = before.get("home", home_score)
        before_away = before.get("away", away_score)
        is_second_yellow = "SECOND" in kind and "YELLOW" in kind
        is_red = "RED" in kind or is_second_yellow
        if is_red:
            player = event.get("player") or event.get("person") or {}
            player_id, player_name = _identity(player)
            red_cards.append({
                "provider": "uefa",
                "match_id": str(match_id),
                "snapshot_at": iso_utc(snapshot_at, field="snapshot_at"),
                "announced_at": announced_at,
                "event_id": str(event.get("id")) if event.get("id") is not None else None,
                "event_type": "second_yellow_red" if is_second_yellow else "red_card",
                "minute": minute,
                "added_time": added,
                "team_id": team_id,
                "team_name": team_name,
                "red_card_side": side,
                "player_id": player_id,
                "player_name": player_name,
                "score_before_home": int(before_home),
                "score_before_away": int(before_away),
            })
        if "GOAL" in kind and "DISALLOWED" not in kind:
            after = _mapping(event.get("score"))
            if after.get("home") is not None and after.get("away") is not None:
                home_score, away_score = int(after["home"]), int(after["away"])
            elif side == "home":
                home_score += 1
            elif side == "away":
                away_score += 1

    referees = normalize_uefa_referees(
        payload, match_id=match_id, snapshot_at=snapshot_at
    )
    return {"red_cards": red_cards, "referees": referees}


def normalize_uefa_referees(
    payload: Mapping[str, Any],
    *,
    match_id: str | int,
    snapshot_at: datetime | str,
) -> list[dict[str, Any]]:
    """Normalize the main referee when included in either UEFA response."""
    if not isinstance(payload, Mapping):
        raise ValueError("unexpected UEFA referee response")
    announced_at = _timestamp(payload, snapshot_at)
    referees: list[dict[str, Any]] = []
    raw_referees = payload.get("referees") or payload.get("officials") or []
    if isinstance(raw_referees, list):
        for official in raw_referees:
            if not isinstance(official, Mapping):
                continue
            role = str(official.get("role") or official.get("type") or "").upper()
            if role not in {"REFEREE", "MAIN_REFEREE"}:
                continue
            person = official.get("person") or official
            referee_id, referee_name = _identity(person)
            referees.append({
                "provider": "uefa",
                "match_id": str(match_id),
                "snapshot_at": iso_utc(snapshot_at, field="snapshot_at"),
                "announced_at": announced_at,
                "referee_id": referee_id,
                "referee_name": referee_name,
                "role": "referee",
            })
    return referees


def fetch_uefa_match_context(
    match_id: str | int,
    *,
    snapshot_at: datetime | str | None = None,
    base_url: str = UEFA_MATCH_URL,
    timeout: float = 30.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Fetch official ``/lineups`` and ``/events`` endpoints for one match."""
    captured = snapshot_at or datetime.now(timezone.utc)
    client = session or requests.Session()
    root = base_url.format(match_id=match_id).rstrip("/")
    headers = {"Accept": "application/json", "User-Agent": "xgedge-point-in-time/1"}
    responses = {}
    for resource in ("lineups", "events"):
        response = client.get(f"{root}/{resource}", headers=headers, timeout=timeout)
        response.raise_for_status()
        responses[resource] = response.json()
    lineups = normalize_uefa_lineups(
        responses["lineups"], match_id=match_id, snapshot_at=captured
    )
    context = normalize_uefa_events(
        responses["events"], match_id=match_id, snapshot_at=captured
    )
    referees = normalize_uefa_referees(
        responses["lineups"], match_id=match_id, snapshot_at=captured
    ) + context["referees"]
    unique_referees: list[dict[str, Any]] = []
    seen_referees: set[tuple[str | None, str | None]] = set()
    for referee in referees:
        identity = (referee.get("referee_id"), referee.get("referee_name"))
        if identity not in seen_referees:
            seen_referees.add(identity)
            unique_referees.append(referee)
    return {
        "lineups": available_snapshot("uefa_lineups", lineups, snapshot_at=captured),
        "red_cards": available_snapshot(
            "uefa_events", context["red_cards"], snapshot_at=captured
        ),
        "referees": available_snapshot(
            "uefa_match", unique_referees, snapshot_at=captured
        ),
    }
