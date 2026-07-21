"""Point-in-time contracts and leakage-safe availability aggregation.

The module deliberately keeps provider data separate from the core match
contract.  Every observation carries the time at which it was known, allowing
callers to reproduce exactly what was available at a pre-match cutoff.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Iterable, Mapping


def as_utc(value: datetime | str, *, field: str) -> datetime:
    """Parse an aware UTC datetime, treating a naive datetime as UTC."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime or ISO-8601 string")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime | str, *, field: str = "datetime") -> str:
    return as_utc(value, field=field).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def unavailable_snapshot(
    provider: str,
    reason: str,
    *,
    snapshot_at: datetime | str,
) -> dict[str, Any]:
    """Return an explicit unavailable state; ``records`` is never an empty list."""
    if not provider.strip() or not reason.strip():
        raise ValueError("provider and reason must be non-empty")
    return {
        "provider": provider,
        "status": "unavailable",
        "reason": reason,
        "snapshot_at": iso_utc(snapshot_at, field="snapshot_at"),
        "records": None,
    }


def available_snapshot(
    provider: str,
    records: Iterable[Mapping[str, Any]],
    *,
    snapshot_at: datetime | str,
) -> dict[str, Any]:
    """Wrap normalized provider records with their point-in-time status."""
    if not provider.strip():
        raise ValueError("provider must be non-empty")
    return {
        "provider": provider,
        "status": "available",
        "reason": None,
        "snapshot_at": iso_utc(snapshot_at, field="snapshot_at"),
        "records": [dict(record) for record in records],
    }


def assert_prematch_snapshot(
    snapshot_at: datetime | str,
    kickoff_utc: datetime | str,
) -> None:
    """Reject information captured after kickoff from pre-match features."""
    snapshot = as_utc(snapshot_at, field="snapshot_at")
    kickoff = as_utc(kickoff_utc, field="kickoff_utc")
    if snapshot >= kickoff:
        raise ValueError(
            "at-or-post-kickoff snapshot cannot be used in pre-match features: "
            f"{iso_utc(snapshot)} >= {iso_utc(kickoff)}"
        )


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and parsed >= 0 else None


def _latest_eligible_records(
    snapshots: Iterable[Mapping[str, Any]],
    *,
    cutoff: datetime,
    kickoff: datetime,
) -> tuple[list[dict[str, Any]], bool]:
    """Return records from the newest available snapshot at or before cutoff."""
    candidates: list[tuple[datetime, list[dict[str, Any]]]] = []
    source_was_available = False
    for snapshot in snapshots:
        status = snapshot.get("status")
        records = snapshot.get("records")
        if status == "unavailable":
            if records is not None:
                raise ValueError("unavailable snapshot records must be None")
            continue
        if status != "available" or not isinstance(records, list):
            raise ValueError("snapshot must have available/list or unavailable/None state")
        captured = as_utc(snapshot.get("snapshot_at"), field="snapshot_at")
        if captured > cutoff:
            continue
        assert_prematch_snapshot(captured, kickoff)
        source_was_available = True
        candidates.append((captured, [dict(record) for record in records]))
    if not candidates:
        return [], source_was_available
    return max(candidates, key=lambda candidate: candidate[0])[1], True


def aggregate_availability_features(
    *,
    team_id: str,
    kickoff_utc: datetime | str,
    cutoff: datetime | str,
    lineup_snapshots: Iterable[Mapping[str, Any]] = (),
    injury_snapshots: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Aggregate features using only snapshots known by a pre-match cutoff.

    Injury counts remain ``None`` whenever the injury provider is unavailable;
    zero is emitted only when an available snapshot explicitly contains no
    matching injuries.
    """
    if not str(team_id).strip():
        raise ValueError("team_id must be non-empty")
    kickoff = as_utc(kickoff_utc, field="kickoff_utc")
    boundary = as_utc(cutoff, field="cutoff")
    if boundary > kickoff:
        raise ValueError("pre-match feature cutoff cannot be after kickoff")

    lineups, lineup_available = _latest_eligible_records(
        lineup_snapshots, cutoff=boundary, kickoff=kickoff
    )
    injuries, injury_available = _latest_eligible_records(
        injury_snapshots, cutoff=boundary, kickoff=kickoff
    )
    team_lineups = [row for row in lineups if str(row.get("team_id")) == str(team_id)]
    team_injuries = [row for row in injuries if str(row.get("team_id")) == str(team_id)]

    starters = [row for row in team_lineups if row.get("lineup_status") == "starter"]
    expected = [_number(row.get("expected_minutes")) for row in team_lineups]
    expected_minutes = sum(value for value in expected if value is not None)
    statuses = {"injured", "doubtful", "suspended", "unavailable"}
    unavailable = [
        row for row in team_injuries if str(row.get("availability_status", "")).lower() in statuses
    ]
    unavailable_expected = [
        _number(row.get("expected_minutes")) for row in unavailable
    ]
    return {
        "team_id": str(team_id),
        "cutoff": iso_utc(boundary, field="cutoff"),
        "lineup_source_available": lineup_available,
        "lineup_confirmed": bool(team_lineups)
        and all(bool(row.get("is_confirmed")) for row in team_lineups),
        "lineup_players": len(team_lineups) if lineup_available else None,
        "confirmed_starters": len(starters) if lineup_available else None,
        "lineup_expected_minutes": expected_minutes if lineup_available else None,
        "injury_source_available": injury_available,
        "unavailable_players": len(unavailable) if injury_available else None,
        "unavailable_expected_minutes": (
            sum(value for value in unavailable_expected if value is not None)
            if injury_available
            else None
        ),
    }
