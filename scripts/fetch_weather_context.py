"""Fetch point-in-time weather for normalized live fixtures."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from xgedge.data.weather import fetch_fixture_weather


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
    output = {"generated_at": captured, "fixtures": {}}
    for fixture in fixtures:
        if not isinstance(fixture, dict) or fixture.get("id") is None:
            continue
        output["fixtures"][str(fixture["id"])] = {
            "weather": fetch_fixture_weather(
                fixture, snapshot_at=captured, session=session, timeout=args.timeout
            )
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    available = sum(
        row["weather"]["status"] == "available" for row in output["fixtures"].values()
    )
    print(f"wrote weather for {available}/{len(output['fixtures'])} fixtures")


if __name__ == "__main__":
    main()
