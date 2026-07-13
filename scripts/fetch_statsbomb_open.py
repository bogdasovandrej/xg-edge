"""Fetch a bounded StatsBomb Open Data snapshot for historical calibration.

With no IDs the command downloads only the small competition catalog.  Passing
competition and season IDs downloads match metadata.  Event and lineup files
are fetched only when one explicit match ID is also supplied.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from xgedge.data.statsbomb_open import (
    SCHEMA_VERSION,
    STATSBOMB_OPEN_DATA_BASE,
    USAGE_MODE,
    fetch_catalog,
    fetch_match_record,
    fetch_matches,
    source_provenance,
)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return parsed


def _resource_url(base_url: str, resource: str) -> str:
    return f"{base_url.rstrip('/')}/{resource}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--competition-id", type=_positive_int)
    parser.add_argument("--season-id", type=_positive_int)
    parser.add_argument("--match-id", type=_positive_int)
    parser.add_argument("--base-url", default=STATSBOMB_OPEN_DATA_BASE)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    if (args.competition_id is None) != (args.season_id is None):
        parser.error("--competition-id and --season-id must be supplied together")
    if args.match_id is not None and args.competition_id is None:
        parser.error("--match-id requires --competition-id and --season-id")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    fetched_at = datetime.now(timezone.utc)
    session = requests.Session()
    # Keep public-data fetches reproducible when a desktop proxy is configured.
    session.trust_env = False
    if args.match_id is not None:
        payload = fetch_match_record(
            args.competition_id,
            args.season_id,
            args.match_id,
            base_url=args.base_url,
            timeout=args.timeout,
            session=session,
            fetched_at=fetched_at,
        )
        mode = "single_match_events_and_lineups"
        count = 1
    elif args.competition_id is not None:
        matches = fetch_matches(
            args.competition_id,
            args.season_id,
            base_url=args.base_url,
            timeout=args.timeout,
            session=session,
        )
        url = _resource_url(
            args.base_url,
            f"matches/{args.competition_id}/{args.season_id}.json",
        )
        payload = {
            "schema_version": SCHEMA_VERSION,
            "usage_mode": USAGE_MODE,
            "current_coverage_guaranteed": False,
            "snapshot_type": "historical_matches",
            "matches": matches,
            "provenance": source_provenance(
                source_urls=[url], fetched_at=fetched_at
            ),
        }
        mode = "historical_matches"
        count = len(matches)
    else:
        competitions = fetch_catalog(
            base_url=args.base_url,
            timeout=args.timeout,
            session=session,
        )
        url = _resource_url(args.base_url, "competitions.json")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "usage_mode": USAGE_MODE,
            "current_coverage_guaranteed": False,
            "snapshot_type": "historical_catalog",
            "competition_seasons": competitions,
            "provenance": source_provenance(
                source_urls=[url], fetched_at=fetched_at
            ),
        }
        mode = "historical_catalog"
        count = len(competitions)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {count} {mode} record(s) to {args.output}")


if __name__ == "__main__":
    main()
