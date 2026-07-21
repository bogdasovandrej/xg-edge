"""Evaluate and register a leak-free PAPER challenger from the archive."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from xgedge.automation.archive import validate_archive
from xgedge.automation.challenger import (
    empty_registry,
    evaluate_temperature_challenger,
    register_challenger,
    validate_registry,
)
from xgedge.simulation.ledger import write_json_atomic


def _read_object(path: Path, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {name}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return dict(value)


def update_files(
    registry_path: Path,
    *,
    archive_path: Path,
    evaluated_at: str | datetime | None = None,
    auto_promote_paper: bool = True,
) -> dict[str, Any]:
    """Register the current challenger; promotion is gated by fixed policy."""
    when = evaluated_at or datetime.now(timezone.utc)
    archive = validate_archive(_read_object(archive_path, name="forecast archive"))
    registry = (
        validate_registry(_read_object(registry_path, name="model registry"))
        if registry_path.exists()
        else empty_registry(created_at=when)
    )
    candidate = evaluate_temperature_challenger(archive, evaluated_at=when)
    updated, operation = register_challenger(
        registry,
        candidate,
        registered_at=when,
        auto_promote_paper=auto_promote_paper,
    )
    validate_registry(updated)
    changed = write_json_atomic(registry_path, updated)
    return {**operation, "registry_changed": changed}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry", type=Path, default=Path("reports/live/model_registry.json")
    )
    parser.add_argument(
        "--archive", type=Path, default=Path("reports/live/forecast_archive.json")
    )
    parser.add_argument("--evaluated-at")
    parser.add_argument(
        "--no-auto-promote-paper",
        action="store_true",
        help="register the challenger but do not move the PAPER champion pointer",
    )
    args = parser.parse_args(argv)
    try:
        result = update_files(
            args.registry,
            archive_path=args.archive,
            evaluated_at=args.evaluated_at,
            auto_promote_paper=not args.no_auto_promote_paper,
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.exit(2, f"Model registry update failed closed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))


if __name__ == "__main__":
    main()
