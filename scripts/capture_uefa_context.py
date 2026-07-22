"""Capture official UEFA prematch line-ups, coaches and referees safely."""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

from xgedge.data.point_in_time import (
    as_utc,
    assert_prematch_snapshot,
    iso_utc,
)
from xgedge.data.uefa_match_context import fetch_uefa_prematch_context


def _read_previous(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"generated_at": None, "fixtures": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("fixtures"), Mapping):
        raise ValueError("context output must contain a fixtures object")
    return deepcopy(dict(payload))


def _snapshot_time(snapshot: Mapping[str, Any] | None) -> datetime | None:
    if not isinstance(snapshot, Mapping) or not snapshot.get("snapshot_at"):
        return None
    try:
        return as_utc(snapshot["snapshot_at"], field="snapshot_at")
    except (TypeError, ValueError):
        return None


def _use_snapshot(
    previous: Any,
    current: Mapping[str, Any],
    *,
    require_records: bool,
) -> dict[str, Any] | None:
    """Prefer a newer usable snapshot; never erase usable data with emptiness."""
    records = current.get("records")
    usable = (
        current.get("status") == "available"
        and isinstance(records, list)
        and (bool(records) or not require_records)
    )
    prior = dict(previous) if isinstance(previous, Mapping) else None
    if not usable:
        return prior
    prior_time = _snapshot_time(prior)
    current_time = _snapshot_time(current)
    if current_time is None:
        return prior
    if prior_time is not None and current_time < prior_time:
        return prior
    return deepcopy(dict(current))


def capture_context(
    fixtures: list[dict[str, Any]],
    previous: Mapping[str, Any] | None,
    *,
    now: str | datetime,
    window_hours: float = 4.0,
    timeout: float = 30.0,
    fetcher: Callable[..., Mapping[str, Any]] = fetch_uefa_prematch_context,
    session: requests.Session | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    captured = as_utc(now, field="now")
    if not 0 < float(window_hours) <= 24:
        raise ValueError("window_hours must be in (0, 24]")
    if not 0 < float(timeout) <= 120:
        raise ValueError("timeout must be in (0, 120]")
    prior_rows = previous.get("fixtures") if isinstance(previous, Mapping) else {}
    prior_rows = prior_rows if isinstance(prior_rows, Mapping) else {}
    output: dict[str, Any] = {
        "generated_at": iso_utc(captured, field="now"),
        "fixtures": {},
    }
    stats = {"eligible": 0, "requested": 0, "lineups": 0, "errors": 0}
    client = session or requests.Session()
    if session is None:
        client.trust_env = False

    for fixture in fixtures:
        if not isinstance(fixture, Mapping) or fixture.get("id") is None:
            continue
        try:
            kickoff = as_utc(fixture.get("kickoff_utc"), field="kickoff_utc")
        except (TypeError, ValueError):
            continue
        if kickoff <= captured:
            continue
        fixture_id = str(fixture["id"])
        row = deepcopy(dict(prior_rows.get(fixture_id, {})))
        output["fixtures"][fixture_id] = row
        if str(fixture.get("source") or "").casefold() != "uefa":
            continue
        stats["eligible"] += 1
        if kickoff - captured > timedelta(hours=float(window_hours)):
            continue
        stats["requested"] += 1
        try:
            context = fetcher(
                fixture_id,
                snapshot_at=captured,
                timeout=float(timeout),
                session=client,
            )
            if not isinstance(context, Mapping):
                raise ValueError("UEFA context fetcher returned a non-object")
            for source_key, target_key, required in (
                ("lineups", "lineups", True),
                ("coaches", "coaches", True),
                ("referees", "referee", True),
            ):
                snapshot = context.get(source_key)
                if not isinstance(snapshot, Mapping):
                    continue
                assert_prematch_snapshot(snapshot.get("snapshot_at"), kickoff)
                selected = _use_snapshot(
                    row.get(target_key), snapshot, require_records=required
                )
                if selected is not None:
                    row[target_key] = selected
            lineup = row.get("lineups")
            records = lineup.get("records") if isinstance(lineup, Mapping) else None
            if isinstance(records, list) and records:
                stats["lineups"] += 1
        except (requests.RequestException, TypeError, ValueError, KeyError):
            stats["errors"] += 1
            # Preserve the last valid prematch snapshot. A transient provider
            # failure must not turn a confirmed line-up back into "unknown".

    return output, stats


def _write_if_changed(path: Path, document: Mapping[str, Any]) -> bool:
    rendered = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--now")
    parser.add_argument("--window-hours", type=float, default=4.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    fixtures = json.loads(args.fixtures.read_text(encoding="utf-8"))
    if not isinstance(fixtures, list):
        raise ValueError("fixtures must contain a JSON list")
    now = args.now or datetime.now(timezone.utc)
    previous = _read_previous(args.output)
    output, stats = capture_context(
        fixtures,
        previous,
        now=now,
        window_hours=args.window_hours,
        timeout=args.timeout,
    )
    if output["fixtures"] == previous.get("fixtures"):
        output["generated_at"] = previous.get("generated_at")
    changed = _write_if_changed(args.output, output)
    print(
        "UEFA prematch context "
        f"requested={stats['requested']}; lineups={stats['lineups']}; "
        f"errors={stats['errors']}; changed={str(changed).lower()}"
    )


if __name__ == "__main__":
    main()
