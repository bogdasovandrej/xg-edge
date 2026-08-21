"""Fetch RPL and top-five domestic cup fixtures from api-football.

Skips silently without ``API_FOOTBALL_KEY`` so scheduled automation stays
fail-closed rather than erroring or inventing an empty-but-successful
snapshot.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from xgedge.data.api_football import SUPPORTED_KEYS, fetch_fixtures


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/live/extra_fixtures.json"))
    parser.add_argument(
        "--competition", action="append", choices=SUPPORTED_KEYS,
        help="competition key; repeatable (default: all supported)",
    )
    parser.add_argument("--as-of")
    parser.add_argument("--to-date")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    api_key = os.environ.get("API_FOOTBALL_KEY", "")
    if not api_key.strip():
        print("API_FOOTBALL_KEY is not configured; RPL/cup fixture refresh skipped")
        return

    document = fetch_fixtures(
        api_key=api_key,
        keys=args.competition or SUPPORTED_KEYS,
        as_of=args.as_of,
        to_date=args.to_date,
        timeout=args.timeout,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(document['fixtures'])} fixtures to {args.output}")


if __name__ == "__main__":
    main()
