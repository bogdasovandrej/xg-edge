"""File-level tests for the deterministic PAPER CLI."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from scripts.update_paper_trading import update_files
from xgedge.simulation.ledger import load_paper_ledger

T0 = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _write(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _payload() -> dict:
    odds, probability = 2.0, .58
    return {
        "paper_candidate_ranking": {
            "schema_version": "paper-candidate-ranking/1.0",
            "status": "PAPER_ONLY",
            "real_money_execution": False,
            "candidates": [{
                "fixture_id": "m1",
                "competition": "UCL",
                "stage": "Qualifying",
                "kickoff_utc": (T0 + timedelta(hours=2)).isoformat(),
                "home": "Home",
                "away": "Away",
                "selection": "П1",
                "outcome": "home",
                "model_probability": probability,
                "break_even_probability": 1 / odds,
                "probability_edge": probability - 1 / odds,
                "odds": odds,
                "bookmaker": "Book A",
                "bookmaker_key": "a",
                "quote_source": "the_odds_api",
                "quote_captured_at": (T0 - timedelta(minutes=5)).isoformat(),
                "point_edge": probability * odds - 1,
                "robust_edge": .07,
                "data_quality_score": 90,
                "market_period": "REGULATION_90_MINUTES",
                "status": "PAPER_ONLY",
                "real_money_eligible": False,
                "rank": 1,
            }],
        }
    }


def test_update_files_creates_valid_ledger_and_second_run_is_byte_idempotent(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "paper.json"
    live_path = tmp_path / "live.json"
    _write(live_path, _payload())

    first = update_files(ledger_path, live_path, now=T0)
    first_bytes = ledger_path.read_bytes()
    second = update_files(ledger_path, live_path, now=T0)

    assert first["ledger_created"] is True
    assert first["enrolled"] == 1
    assert second["status"] == "unchanged"
    assert second["ledger_changed"] is False
    assert ledger_path.read_bytes() == first_bytes
    assert load_paper_ledger(ledger_path)["paper_trading"]["totals"]["open_bets"] == 3
    public = json.loads(live_path.read_text(encoding="utf-8"))
    assert public["paper_trading"]["totals"]["open_bets"] == 3
    assert public["paper_trading"]["real_money_execution"] is False


def test_invalid_existing_ledger_is_never_overwritten(tmp_path: Path) -> None:
    ledger_path = tmp_path / "paper.json"
    live_path = tmp_path / "live.json"
    bad = b'{"schema_version":"wrong"}\n'
    ledger_path.write_bytes(bad)
    _write(live_path, _payload())

    with pytest.raises(ValueError, match="fields mismatch"):
        update_files(ledger_path, live_path, now=T0)
    assert ledger_path.read_bytes() == bad
