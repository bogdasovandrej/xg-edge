"""Settle prospective forecasts from official FIFA and UEFA results."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from xgedge.data.official_results import fetch_tracked_results
from xgedge.data.point_in_time import as_utc
from xgedge.evaluation.prospective import (
    apply_summary_to_live_payload,
    finalize_clv_after_kickoff,
    prospective_summary,
    settle_results,
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_if_changed(path: Path, document: Mapping[str, Any]) -> bool:
    rendered = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
    return True


def settle_files(
    ledger_path: Path,
    live_payload_path: Path,
    *,
    now: str | datetime | None = None,
    timeout: float = 30.0,
    fetcher: Callable[..., Mapping[str, Any]] = fetch_tracked_results,
) -> dict[str, Any]:
    """Fetch and persist new settlements without touching any CLV fields."""
    if not ledger_path.exists():
        return {"status": "skipped", "reason": "ledger_missing", "settled": 0}
    if not live_payload_path.exists():
        return {"status": "skipped", "reason": "live_payload_missing", "settled": 0}
    try:
        ledger, live_payload = _read(ledger_path), _read(live_payload_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "skipped", "reason": f"invalid_local_json: {exc}", "settled": 0}
    if not isinstance(ledger, Mapping) or not isinstance(live_payload, Mapping):
        return {"status": "skipped", "reason": "invalid_local_document", "settled": 0}
    if not isinstance(ledger.get("fixtures"), Mapping):
        return {"status": "skipped", "reason": "invalid_ledger_fixtures", "settled": 0}

    settled_at = as_utc(now or datetime.now(timezone.utc), field="now")
    try:
        snapshot = fetcher(ledger, now=settled_at, timeout=timeout)
    except (OSError, TypeError, ValueError) as exc:
        return {"status": "skipped", "reason": f"result_fetch_failed: {exc}", "settled": 0}
    results = snapshot.get("results") if isinstance(snapshot, Mapping) else None
    results = results if isinstance(results, list) else []
    before = {
        str(key)
        for key, value in ledger["fixtures"].items()
        if isinstance(value, Mapping) and isinstance(value.get("result"), Mapping)
    }
    updated = finalize_clv_after_kickoff(ledger, finalized_at=settled_at)
    if results:
        updated = settle_results(updated, results, settled_at=settled_at)
    after = {
        str(key)
        for key, value in updated.get("fixtures", {}).items()
        if isinstance(value, Mapping) and isinstance(value.get("result"), Mapping)
    }
    public = apply_summary_to_live_payload(live_payload, prospective_summary(updated))
    ledger_changed = _write_if_changed(ledger_path, updated)
    live_changed = _write_if_changed(live_payload_path, public)
    return {
        "status": str(snapshot.get("status") or "unknown"),
        "reason": None,
        "requested": len(snapshot.get("requested_fixture_ids") or []),
        "fetched_results": len(results),
        "settled": len(after - before),
        "errors": len(snapshot.get("errors") or []),
        "ledger_changed": ledger_changed,
        "live_payload_changed": live_changed,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--live-payload", type=Path, required=True)
    parser.add_argument("--now")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    result = settle_files(
        args.ledger,
        args.live_payload,
        now=args.now,
        timeout=args.timeout,
    )
    if result["status"] == "skipped":
        print(f"Result settlement skipped safely: {result['reason']}")
        return
    print(
        "Official result settlement "
        f"status={result['status']}; requested={result['requested']}; "
        f"fetched={result['fetched_results']}; newly_settled={result['settled']}; "
        f"errors={result['errors']}"
    )


if __name__ == "__main__":
    main()
