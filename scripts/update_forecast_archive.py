"""Update the immutable forecast archive from fixtures, forecasts and results."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from xgedge.automation.archive import empty_archive, update_archive, validate_archive
from xgedge.data.official_results import fetch_tracked_results
from xgedge.simulation.ledger import write_json_atomic


def _read_object(path: Path, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {name}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return dict(value)


def _read_fixtures(path: Path) -> list[Mapping[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read fixture snapshot: {exc}") from exc
    source_rows = value.get("fixtures", value) if isinstance(value, Mapping) else value
    if not isinstance(source_rows, list):
        raise ValueError("fixture snapshot must be a list or contain a fixtures array")
    return [row for row in source_rows if isinstance(row, Mapping)]


def update_files(
    archive_path: Path,
    *,
    fixtures_path: Path | None = None,
    live_payload_path: Path | None = None,
    observed_at: str | datetime | None = None,
    fetch_results: bool = False,
    timeout: float = 30.0,
    result_fetcher: Callable[..., Mapping[str, Any]] = fetch_tracked_results,
) -> dict[str, Any]:
    """Run one archive cycle and atomically persist the validated document."""
    when = observed_at or datetime.now(timezone.utc)
    archive = (
        _read_object(archive_path, name="forecast archive")
        if archive_path.exists()
        else empty_archive(created_at=when)
    )
    fixtures: list[Mapping[str, Any]] = []
    if fixtures_path is not None and fixtures_path.exists():
        fixtures = _read_fixtures(fixtures_path)
    live_payload = (
        _read_object(live_payload_path, name="live payload")
        if live_payload_path is not None and live_payload_path.exists()
        else None
    )
    updated, operation = update_archive(
        archive,
        fixtures=fixtures,
        live_payload=live_payload,
        observed_at=when,
        fetch_results=fetch_results,
        timeout=timeout,
        result_fetcher=result_fetcher,
    )
    validate_archive(updated)
    changed = write_json_atomic(archive_path, updated)
    return {**operation, "archive_changed": changed}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, default=Path("reports/live/forecast_archive.json")
    )
    parser.add_argument("--fixtures", type=Path, default=Path("reports/live/current_fixtures.json"))
    parser.add_argument("--live-payload", type=Path, default=Path("reports/live_predictions.json"))
    parser.add_argument("--observed-at")
    parser.add_argument("--fetch-results", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        result = update_files(
            args.archive,
            fixtures_path=args.fixtures,
            live_payload_path=args.live_payload,
            observed_at=args.observed_at,
            fetch_results=args.fetch_results,
            timeout=args.timeout,
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.exit(2, f"Forecast archive update failed closed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))


if __name__ == "__main__":
    main()
