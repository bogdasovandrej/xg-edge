"""Optional availability provider contracts.

Sportmonks is opt-in and no request is made without an explicit argument or
environment token.  Opta intentionally remains a contract until licensed.
FBref is not used as an injury or line-up scraper.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping, Protocol, runtime_checkable

import requests

from xgedge.data.point_in_time import available_snapshot, iso_utc, unavailable_snapshot

SPORTMONKS_INJURIES_URL = "https://api.sportmonks.com/v3/football/injuries"


@runtime_checkable
class AvailabilityProvider(Protocol):
    """Point-in-time provider contract shared by licensed future adapters."""

    name: str

    def fetch_snapshot(
        self,
        *,
        team_ids: list[str] | None = None,
        snapshot_at: datetime | str | None = None,
    ) -> Mapping[str, Any]: ...


def _name(entity: Any) -> tuple[str | None, str | None]:
    if not isinstance(entity, Mapping):
        return None, None
    identifier = entity.get("id")
    name = entity.get("display_name") or entity.get("common_name") or entity.get("name")
    return str(identifier) if identifier is not None else None, str(name) if name else None


def normalize_sportmonks_injury(
    injury: Mapping[str, Any], *, snapshot_at: datetime | str
) -> dict[str, Any] | None:
    """Normalize a Sportmonks-compatible injury object."""
    if not isinstance(injury, Mapping):
        return None
    participant = injury.get("participant") or injury.get("team") or {}
    player = injury.get("player") or {}
    team_id, team_name = _name(participant)
    player_id, player_name = _name(player)
    team_id = team_id or (
        str(injury.get("participant_id")) if injury.get("participant_id") is not None else None
    )
    player_id = player_id or (
        str(injury.get("player_id")) if injury.get("player_id") is not None else None
    )
    if team_id is None or player_id is None:
        return None
    raw_status = str(injury.get("status") or injury.get("type") or "injured").lower()
    status = "suspended" if "suspend" in raw_status else (
        "doubtful" if "doubt" in raw_status else "injured"
    )
    expected = injury.get("expected_minutes")
    try:
        expected_minutes = float(expected) if expected is not None else None
    except (TypeError, ValueError):
        expected_minutes = None
    if expected_minutes is not None and (
        not isfinite(expected_minutes) or expected_minutes < 0
    ):
        expected_minutes = None
    announced = injury.get("updated_at") or injury.get("created_at") or snapshot_at
    return {
        "provider": "sportmonks",
        "snapshot_at": iso_utc(snapshot_at, field="snapshot_at"),
        "announced_at": iso_utc(announced, field="announced_at"),
        "team_id": team_id,
        "team_name": team_name,
        "player_id": player_id,
        "player_name": player_name,
        "availability_status": status,
        "expected_minutes": expected_minutes,
        "reason": injury.get("reason") or injury.get("description"),
        "estimated_return": injury.get("expected_return") or injury.get("end_date"),
    }


class SportmonksInjuryProvider:
    """Read-only Sportmonks-compatible adapter with explicit unavailability."""

    name = "sportmonks"

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = SPORTMONKS_INJURIES_URL,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self._token = token or os.getenv("SPORTMONKS_API_TOKEN")
        self._base_url = base_url
        self._timeout = timeout
        self._session = session

    def fetch_snapshot(
        self,
        *,
        team_ids: list[str] | None = None,
        snapshot_at: datetime | str | None = None,
    ) -> Mapping[str, Any]:
        captured = snapshot_at or datetime.now(timezone.utc)
        if not self._token:
            return unavailable_snapshot(
                self.name, "missing_api_token", snapshot_at=captured
            )
        client = self._session or requests.Session()
        params: dict[str, Any] = {"include": "player;participant"}
        if team_ids:
            params["filter[participantIds]"] = ",".join(map(str, team_ids))
        response = client.get(
            self._base_url,
            params=params,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "xgedge-point-in-time/1",
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(rows, list):
            raise ValueError("unexpected Sportmonks response: data array is missing")
        records = [
            normalized
            for row in rows
            if isinstance(row, Mapping)
            for normalized in [normalize_sportmonks_injury(row, snapshot_at=captured)]
            if normalized is not None
        ]
        return available_snapshot(self.name, records, snapshot_at=captured)


class OptaProviderContract:
    """Non-network placeholder documenting the contract pending an Opta licence."""

    name = "opta"

    def fetch_snapshot(
        self,
        *,
        team_ids: list[str] | None = None,
        snapshot_at: datetime | str | None = None,
    ) -> Mapping[str, Any]:
        del team_ids
        return unavailable_snapshot(
            self.name,
            "licensed_provider_not_configured",
            snapshot_at=snapshot_at or datetime.now(timezone.utc),
        )
