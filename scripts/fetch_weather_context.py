"""Fetch point-in-time weather for normalized live fixtures."""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import requests

from xgedge.data.weather import fetch_fixture_weather


def _previous(path: Path) -> dict:
    if not path.exists():
        return {"fixtures": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("fixtures"), dict):
        raise ValueError("context output must contain a fixtures object")
    return payload


def merge_weather_context(previous: dict, fixtures: list[dict], captured: str, *, session, timeout: float) -> dict:
    prior_rows = previous.get("fixtures", {}) if isinstance(previous, dict) else {}
    output = {"generated_at": captured, "fixtures": {}}
    for fixture in fixtures:
        if not isinstance(fixture, dict) or fixture.get("id") is None:
            continue
        fixture_id = str(fixture["id"])
        row = deepcopy(prior_rows.get(fixture_id, {}))
        current = fetch_fixture_weather(
            fixture, snapshot_at=captured, session=session, timeout=timeout
        )
        # A transient weather failure must not delete a valid lineup, referee,
        # coach, or prior weather snapshot from the shared context document.
        if current.get("status") == "available" or "weather" not in row:
            row["weather"] = current
        output["fixtures"][fixture_id] = row
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot-at")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    captured = args.snapshot_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    fixtures = json.loads(args.fixtures.read_text(encoding="utf-8"))
    if not isinstance(fixtures, list):
        raise ValueError("fixtures must contain a JSON list")
    session = requests.Session()
    session.trust_env = False
    output = merge_weather_context(
        _previous(args.output), fixtures, captured,
        session=session, timeout=args.timeout,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    available = sum(
        row["weather"]["status"] == "available" for row in output["fixtures"].values()
    )
    print(f"wrote weather for {available}/{len(output['fixtures'])} fixtures")


if __name__ == "__main__":
    main()
