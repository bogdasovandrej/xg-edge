"""Capture official bookmaker odds and update the prospective CLV ledger."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import requests

from xgedge.data.bookmaker_odds import (
    OddsApiIoProvider,
    SPORT_KEYS,
    TheOddsApiProvider,
    apply_odds_snapshot_to_live_payload,
    merge_odds_snapshots,
)
from xgedge.data.point_in_time import as_utc
from xgedge.evaluation.prospective import (
    apply_summary_to_live_payload,
    finalize_clv_after_kickoff,
    ingest_odds_snapshot,
    new_ledger,
    prospective_summary,
)
from xgedge.decision.ranking import rank_paper_candidates


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, document: Mapping[str, Any]) -> bool:
    rendered = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return True


def sport_key_for_fixture(fixture: Mapping[str, Any]) -> str | None:
    competition = str(fixture.get("competition") or "")
    if "World Cup" in competition:
        return SPORT_KEYS["FIFA World Cup 2026"]
    if "Champions League" in competition:
        return SPORT_KEYS["UEFA Champions League"]
    for name in ("Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1"):
        if name in competition:
            return SPORT_KEYS[name]
    return None


def required_sport_keys(
    fixtures: list[dict], ledger: Mapping[str, Any], *, now: datetime,
    closing_window_minutes: int, discovery_days: int,
    last_snapshot: Mapping[str, Any] | None = None,
    discovery_cooldown_hours: int = 24,
    include_discovery: bool = True,
) -> list[str]:
    tracked = ledger.get("fixtures", {}) if isinstance(ledger, Mapping) else {}
    keys: set[str] = set()
    recently_polled: set[str] = set()
    if isinstance(last_snapshot, Mapping):
        poll_times = last_snapshot.get("sport_poll_times")
        if isinstance(poll_times, Mapping):
            for sport_key, row in poll_times.items():
                if not isinstance(row, Mapping) or not row.get("received_at"):
                    continue
                previous = as_utc(row["received_at"], field="received_at")
                if now - previous < timedelta(hours=discovery_cooldown_hours):
                    recently_polled.add(str(sport_key))
        elif last_snapshot.get("snapshot_at"):
            previous = as_utc(last_snapshot["snapshot_at"], field="snapshot_at")
            if now - previous < timedelta(hours=discovery_cooldown_hours):
                recently_polled = {
                    str(key) for key in last_snapshot.get("requested_sport_keys", [])
                }
    for fixture in fixtures:
        if not fixture.get("id") or not fixture.get("kickoff_utc"):
            continue
        kickoff = as_utc(fixture["kickoff_utc"], field="kickoff_utc")
        if kickoff <= now:
            continue
        fixture_id = str(fixture["id"])
        entry = tracked.get(fixture_id) if isinstance(tracked, Mapping) else None
        needs_discovery = entry is None and kickoff - now <= timedelta(days=discovery_days)
        needs_close = kickoff - now <= timedelta(minutes=closing_window_minutes)
        key = sport_key_for_fixture(fixture)
        if key and (
            needs_close
            or (
                include_discovery
                and needs_discovery
                and key not in recently_polled
            )
        ):
            keys.add(key)
    return sorted(keys)


def quota_request_mode(
    snapshot: Mapping[str, Any] | None,
    *,
    now: datetime,
    reserve: int = 25,
    probe_days: int = 7,
) -> str:
    """Return normal, closing_only, probe or blocked from persisted quota."""
    if not isinstance(snapshot, Mapping):
        return "normal"
    quota = snapshot.get("quota")
    remaining = quota.get("remaining") if isinstance(quota, Mapping) else None
    if isinstance(remaining, bool) or not isinstance(remaining, int):
        return "normal"
    reset = quota.get("reset") if isinstance(quota, Mapping) else None
    if reset:
        try:
            if now >= as_utc(reset, field="quota.reset"):
                return "normal"
        except (TypeError, ValueError):
            # A malformed optional provider header must not break the monitor;
            # the conservative persisted-quota policy below still applies.
            pass
    if remaining > reserve:
        return "normal"
    if remaining > 0:
        return "closing_only"
    captured = snapshot.get("snapshot_at")
    if captured and now - as_utc(captured, field="snapshot_at") >= timedelta(days=probe_days):
        return "probe"
    return "blocked"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--top-five-fixtures", type=Path)
    parser.add_argument("--live-payload", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--snapshot-output", type=Path, required=True)
    parser.add_argument("--now")
    parser.add_argument("--closing-window-minutes", type=int, default=60)
    parser.add_argument("--discovery-days", type=int, default=14)
    parser.add_argument("--discovery-cooldown-hours", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--quota-reserve", type=int, default=25)
    parser.add_argument("--quota-probe-days", type=int, default=7)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    now = as_utc(args.now or datetime.now(timezone.utc), field="now")
    fixtures = _read(args.fixtures)
    if args.top_five_fixtures and args.top_five_fixtures.exists():
        top_five = _read(args.top_five_fixtures)
        if not isinstance(top_five, Mapping) or not isinstance(top_five.get("fixtures"), list):
            raise ValueError("top-five fixture document must contain a fixtures list")
        if not isinstance(fixtures, list):
            raise ValueError("fixtures must contain a list")
        known_ids = {str(row.get("id")) for row in fixtures if isinstance(row, Mapping)}
        fixtures = fixtures + [
            row for row in top_five["fixtures"]
            if isinstance(row, Mapping) and str(row.get("id")) not in known_ids
        ]
    live_payload = _read(args.live_payload)
    ledger = _read(args.ledger) if args.ledger.exists() else new_ledger(updated_at=now)
    finalized = finalize_clv_after_kickoff(ledger, finalized_at=now)
    if finalized != ledger:
        ledger = finalized
        _write(args.ledger, ledger)
        live_payload = apply_summary_to_live_payload(
            live_payload, prospective_summary(ledger)
        )
        live_payload["paper_candidate_ranking"] = rank_paper_candidates(live_payload)
        _write(args.live_payload, live_payload)
    if not isinstance(fixtures, list):
        raise ValueError("fixtures must contain a list")
    odds_api_io_key = os.getenv("ODDS_API_IO_KEY")
    legacy_api_key = os.getenv("THE_ODDS_API_KEY")
    if not odds_api_io_key and not legacy_api_key:
        print(
            "ODDS_API_IO_KEY and THE_ODDS_API_KEY are not configured; "
            "no odds request was made"
        )
        return
    if odds_api_io_key:
        provider = OddsApiIoProvider(api_key=odds_api_io_key, timeout=args.timeout)
    else:
        provider = TheOddsApiProvider(api_key=legacy_api_key, timeout=args.timeout)
    stored_snapshot = _read(args.snapshot_output) if args.snapshot_output.exists() else None
    last_snapshot = (
        stored_snapshot
        if isinstance(stored_snapshot, Mapping)
        and stored_snapshot.get("provider") == provider.name
        else None
    )
    quota_mode = quota_request_mode(
        last_snapshot,
        now=now,
        reserve=args.quota_reserve,
        probe_days=args.quota_probe_days,
    )
    keys = required_sport_keys(
        fixtures, ledger, now=now,
        closing_window_minutes=args.closing_window_minutes,
        discovery_days=args.discovery_days,
        last_snapshot=last_snapshot,
        discovery_cooldown_hours=args.discovery_cooldown_hours,
        include_discovery=quota_mode == "normal",
    )
    if quota_mode == "probe" and not keys:
        keys = required_sport_keys(
            fixtures, ledger, now=now,
            closing_window_minutes=args.closing_window_minutes,
            discovery_days=args.discovery_days,
            last_snapshot=None,
            discovery_cooldown_hours=args.discovery_cooldown_hours,
        )[:1]
    if quota_mode == "blocked":
        keys = []
    if args.force and not keys:
        keys = sorted({key for fixture in fixtures for key in [sport_key_for_fixture(fixture)] if key})
    if not keys:
        if last_snapshot is not None:
            refreshed = apply_odds_snapshot_to_live_payload(
                live_payload, last_snapshot, now=now
            )
            refreshed = apply_summary_to_live_payload(
                refreshed, prospective_summary(ledger)
            )
            refreshed["paper_candidate_ranking"] = rank_paper_candidates(refreshed)
            _write(args.live_payload, refreshed)
        print(
            "No bookmaker request required "
            f"(quota_mode={quota_mode}); public TTL refreshed"
        )
        return

    session = requests.Session()
    session.trust_env = False
    provider.session = session
    snapshot = provider.fetch_snapshot(
        sport_keys=keys,
        fixtures=fixtures,
        snapshot_at=now if args.now else None,
    )
    rolling = merge_odds_snapshots(last_snapshot, snapshot)
    _write(args.snapshot_output, rolling)
    updated = (
        ingest_odds_snapshot(
            ledger, snapshot, fixtures=fixtures, live_payload=live_payload,
            closing_window_minutes=args.closing_window_minutes,
        )
        if snapshot.get("status") == "available"
        else ledger
    )
    _write(args.ledger, updated)
    public_now = now if args.now else datetime.now(timezone.utc)
    public = apply_odds_snapshot_to_live_payload(
        live_payload, rolling, now=public_now
    )
    public = apply_summary_to_live_payload(public, prospective_summary(updated))
    public["paper_candidate_ranking"] = rank_paper_candidates(public)
    public["odds_snapshot_at"] = rolling.get("snapshot_at")
    _write(args.live_payload, public)
    matched = sum(record.get("fixture_id") is not None for record in snapshot.get("records", []))
    print(
        f"Captured {matched} matched events; quota_mode={quota_mode}; "
        f"prospective CLV n={updated['gate']['clv']['n']}"
    )


if __name__ == "__main__":
    main()
