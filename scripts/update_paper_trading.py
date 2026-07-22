"""Update the offline PAPER ledger from local predictions and official results."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from xgedge.simulation.ledger import (
    load_paper_ledger,
    new_paper_ledger,
    read_json_object,
    update_paper_ledger,
    write_json_atomic,
)


def update_files(
    ledger_path: Path,
    live_payload_path: Path,
    *,
    prospective_ledger_path: Path | None = None,
    results_path: Path | None = None,
    now: str | datetime | None = None,
) -> dict[str, Any]:
    """Run one fail-closed, atomic PAPER update without network access."""
    run_at = now or datetime.now(timezone.utc)
    live_payload = read_json_object(live_payload_path, name="live payload")
    prospective: Mapping[str, Any] | None = None
    if prospective_ledger_path is not None:
        prospective = read_json_object(
            prospective_ledger_path, name="prospective CLV ledger"
        )
    results: Mapping[str, Any] | None = None
    if results_path is not None:
        results = read_json_object(results_path, name="official results map")
    existed = ledger_path.exists()
    ledger = (
        load_paper_ledger(ledger_path)
        if existed
        else new_paper_ledger(created_at=run_at)
    )
    updated, operation = update_paper_ledger(
        ledger,
        live_payload,
        now=run_at,
        prospective_ledger=prospective,
        official_results=results,
    )
    changed = write_json_atomic(ledger_path, updated)
    public_payload = dict(live_payload)
    public_payload["paper_trading"] = updated["paper_trading"]
    live_changed = write_json_atomic(live_payload_path, public_payload)
    return {
        **operation,
        "ledger_created": not existed,
        "ledger_changed": changed,
        "live_payload_changed": live_changed,
        "paper_trading": updated["paper_trading"],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger", type=Path, default=Path("reports/live/paper_trading.json")
    )
    parser.add_argument(
        "--live-payload", type=Path, default=Path("reports/live_predictions.json")
    )
    parser.add_argument("--prospective-ledger", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--now", help="aware ISO-8601 timestamp for deterministic runs")
    args = parser.parse_args(argv)
    try:
        result = update_files(
            args.ledger,
            args.live_payload,
            prospective_ledger_path=args.prospective_ledger,
            results_path=args.results,
            now=args.now,
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.exit(2, f"PAPER update failed closed: {exc}\n")
    print(json.dumps({
        "status": result["status"],
        "ledger_created": result["ledger_created"],
        "ledger_changed": result["ledger_changed"],
        "enrolled": result["enrolled"],
        "settled": result["settled"],
        "candidate_rejections": result["candidate_rejections"],
    }, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
