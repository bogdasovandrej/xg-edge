"""Append captured bookmaker prices to the store and screen them for movement.

The market monitor captures a fresh snapshot every run and overwrites the
snapshot file. That is enough to price a bet today and useless for asking
whether a price moved, so this script appends each observation to the SQLite
store and then compares the newest observation against the earliest stored one
for the same market and book.

Runs after the capture step in the market-monitor workflow. Without a stored
history it produces an empty feed rather than a fabricated delta.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from xgedge.decision.screener import movement_top, screen_quote_history
from xgedge.storage.db import (
    DEFAULT_PATH,
    market_id,
    quote_history,
    record_quote,
    store,
    upsert_fixture,
    upsert_market,
)

# Only markets whose identity is unambiguous across books are recorded here.
# A totals line differs between books, so it is keyed by its line.
H2H_OUTCOMES = ("home", "draw", "away")


def _price(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    price = float(value)
    return price if price > 1.0 else None


def record_snapshot(connection: Any, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Append every usable price in one capture; returns what was written."""
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise ValueError("odds snapshot has no records array")
    checked_at = str(snapshot.get("snapshot_at") or "")
    if not checked_at:
        raise ValueError("odds snapshot has no snapshot_at timestamp")

    written = 0
    market_keys: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        fixture_id = str(record.get("fixture_id") or "").strip()
        if not fixture_id:
            continue
        upsert_fixture(connection, {
            "id": fixture_id,
            "competition": record.get("sport_key"),
            "home": record.get("home"),
            "away": record.get("away"),
            "kickoff_utc": record.get("commence_time"),
            "status": record.get("match_status") or "scheduled",
        })
        for book in record.get("bookmakers", []) or []:
            if not isinstance(book, Mapping):
                continue
            name = str(book.get("title") or book.get("key") or "").strip()
            markets = book.get("markets")
            h2h = markets.get("h2h") if isinstance(markets, Mapping) else None
            if not name or not isinstance(h2h, Mapping):
                continue
            for outcome in H2H_OUTCOMES:
                price = _price(h2h.get(outcome))
                if price is None:
                    continue
                key = market_id(fixture_id, "1x2", outcome, None)
                upsert_market(connection, {
                    "fixture_id": fixture_id, "family": "1x2",
                    "selection": outcome, "line": None,
                    "period": "90M", "calc_mode": "BINARY",
                })
                record_quote(
                    connection,
                    market_key=key,
                    bookmaker=name,
                    odds=price,
                    checked_at=checked_at,
                    source=str(record.get("source_provider") or snapshot.get("provider") or ""),
                )
                market_keys.add(key)
                written += 1
    return {"observations_written": written, "market_keys": sorted(market_keys)}


def screen_all(connection: Any, market_keys: list[str]) -> dict[str, Any]:
    """Screen every recorded market/book pair that has a stored reference."""
    screened: list[dict[str, Any]] = []
    for key in market_keys:
        history = quote_history(connection, key)
        books = {str(row.get("bookmaker") or "") for row in history}
        for book in sorted(books):
            result = screen_quote_history(
                [row for row in history if row.get("bookmaker") == book]
            )
            if result is not None:
                screened.append(result)
    return movement_top(screened)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=Path("reports/live/bookmaker_odds.json"))
    parser.add_argument("--database", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--feed-output", type=Path, default=Path("reports/live/screener_feed.json"))
    args = parser.parse_args(argv)

    if not args.snapshot.exists():
        print(f"{args.snapshot} not found; quote recording skipped")
        return
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))

    with store(args.database) as connection:
        written = record_snapshot(connection, snapshot)
        feed = screen_all(connection, written["market_keys"])

    args.feed_output.parent.mkdir(parents=True, exist_ok=True)
    args.feed_output.write_text(
        json.dumps(feed, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"recorded {written['observations_written']} observations across "
        f"{len(written['market_keys'])} markets; {len(feed['rows'])} moved"
    )


if __name__ == "__main__":
    main()
