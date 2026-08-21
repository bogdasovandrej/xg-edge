"""Capture a multi-bookmaker snapshot for the leave-one-out consensus.

The primary capture stays on Odds-API.io because the prospective CLV ledger is
keyed to that provider, and changing it mid-protocol would break the frozen
comparison. But the connected Odds-API.io plan returns at most two books, and
a consensus built from one remaining book is not a consensus: every market
came back INSUFFICIENT_BOOKS.

The Odds API's ``eu`` region returns every bookmaker it carries for an event,
so this script takes a second, independent snapshot purely for the consensus
surface. It touches neither the CLV ledger nor the PAPER ranking; it only
writes a file the payload reads when scoring bookmaker disagreement.

Without ``THE_ODDS_API_KEY`` it makes no network call at all.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from xgedge.data.bookmaker_odds import TheOddsApiProvider

# Import the fixture-to-sport mapping from the primary capture so the two
# snapshots can never disagree about which competition a fixture belongs to.
from scripts.capture_bookmaker_odds import sport_key_for_fixture


def _read(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture_rows(document: Any) -> list[dict[str, Any]]:
    rows = document.get("fixtures") if isinstance(document, Mapping) else document
    return [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def capture(
    *,
    api_key: str,
    fixtures: list[dict[str, Any]],
    snapshot_at: str | datetime | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch every EU bookmaker's 1X2 prices for the given fixtures."""
    sport_keys = sorted({
        key for key in (sport_key_for_fixture(row) for row in fixtures) if key
    })
    provider = TheOddsApiProvider(api_key=api_key, timeout=timeout)
    snapshot = provider.fetch_snapshot(
        sport_keys=sport_keys,
        fixtures=fixtures,
        snapshot_at=snapshot_at,
    )
    books = [
        len(record.get("bookmakers") or [])
        for record in (snapshot.get("records") or [])
        if isinstance(record, Mapping)
    ]
    snapshot["consensus_capture"] = {
        "purpose": "leave_one_out_bookmaker_consensus_only",
        "excluded_from": ["prospective_clv_ledger", "paper_candidate_ranking"],
        "records": len(books),
        "median_books_per_record": sorted(books)[len(books) // 2] if books else 0,
        "records_with_three_or_more_books": sum(1 for count in books if count >= 3),
    }
    return snapshot


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path("reports/live/current_fixtures.json"))
    parser.add_argument(
        "--top-five-fixtures", type=Path, default=Path("reports/live/top5_fixtures.json")
    )
    parser.add_argument("--output", type=Path, default=Path("reports/live/consensus_odds.json"))
    parser.add_argument("--snapshot-at")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    api_key = os.getenv("THE_ODDS_API_KEY", "")
    if not api_key.strip():
        print("THE_ODDS_API_KEY is not configured; consensus capture skipped")
        return

    fixtures = _fixture_rows(_read(args.fixtures))
    fixtures.extend(_fixture_rows(_read(args.top_five_fixtures)))
    if not fixtures:
        print("no fixtures available; consensus capture skipped")
        return

    snapshot = capture(
        api_key=api_key,
        fixtures=fixtures,
        snapshot_at=args.snapshot_at or datetime.now(timezone.utc),
        timeout=args.timeout,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    summary = snapshot.get("consensus_capture", {})
    print(
        f"wrote {summary.get('records', 0)} records to {args.output}; "
        f"{summary.get('records_with_three_or_more_books', 0)} have 3+ books"
    )


if __name__ == "__main__":
    main()
