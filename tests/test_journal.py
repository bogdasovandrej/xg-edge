"""The results journal: what calibration needs must survive the model changing."""
from __future__ import annotations

import pytest

from scripts.update_journal import (
    journal_positions,
    journal_settlements,
    journal_value_calcs,
)
from xgedge.storage.db import calibration_buckets, store


@pytest.fixture()
def connection(tmp_path):
    with store(tmp_path / "journal.db") as conn:
        yield conn


def _payload() -> dict:
    return {
        "generated_at": "2026-08-20T12:00:00Z",
        "forecasts": [{
            "id": "fx1",
            "competition": "UEFA Europa League",
            "stage": "Qualifying",
            "home": "A", "away": "B",
            "kickoff_utc": "2026-08-25T18:00:00Z",
            "details": {
                "market_candidates": [
                    {
                        "market": "1x2", "outcome": "home", "line": None,
                        "fair": 2.00, "min_entry": 2.16, "value_pct": 12.5,
                        "gate_price": True, "value_status": "APPROVED",
                        "calc_mode": "BINARY",
                    },
                    {
                        "market": "totals", "outcome": "under", "line": 2.5,
                        "fair": 1.90, "min_entry": 2.05, "value_pct": -3.2,
                        "gate_price": False, "value_status": "BELOW_MIN",
                        "calc_mode": "BINARY",
                    },
                ]
            },
        }],
    }


def _ledger() -> dict:
    return {
        "enrollments": {
            "cand1": {
                "fixture_id": "fx1", "competition": "UEFA Europa League",
                "home": "A", "away": "B", "kickoff_utc": "2026-08-25T18:00:00Z",
                "market": "1x2", "outcome": "home", "line": None,
                "model_probability": 0.55, "odds": 2.10,
                "bookmaker": "Unibet", "quote_source": "odds_api_io",
                "enrolled_at": "2026-08-20T12:05:00Z", "data_quality_score": 70,
            }
        },
        "settlements": {},
    }


def test_rejected_candidates_are_journalled_too(connection) -> None:
    """Without the rejects there is no way to tell if the gate was too tight."""
    written = journal_value_calcs(connection, _payload())
    assert written == 2
    rows = connection.execute(
        "SELECT status, value_pct, gate_price FROM value_calc ORDER BY value_pct DESC"
    ).fetchall()
    assert [row["status"] for row in rows] == ["APPROVED", "BELOW_MIN"]
    assert rows[0]["gate_price"] == 1
    assert rows[1]["gate_price"] == 0


def test_position_stores_the_fair_price_believed_at_entry(connection) -> None:
    assert journal_positions(connection, _ledger()) == 1
    row = connection.execute(
        "SELECT fair, min_entry, value_pct, status FROM value_calc WHERE status='ENROLLED'"
    ).fetchone()
    # model_probability is conditional on no push, so 1/p is the contract's
    # 1 + L/W, and min_entry is that times 1.08.
    assert row["fair"] == pytest.approx(1 / 0.55)
    assert row["min_entry"] == pytest.approx((1 / 0.55) * 1.08)
    assert row["value_pct"] == pytest.approx((0.55 * 2.10 - 1) * 100)


def test_entry_analysis_is_versioned_and_sums_to_one(connection) -> None:
    journal_positions(connection, _ledger())
    row = connection.execute("SELECT * FROM analysis").fetchone()
    total = row["central_win"] + row["central_push"] + row["central_loss"]
    assert total == pytest.approx(1.0)
    assert row["superseded_by"] is None


def test_settlement_keeps_entry_fair_and_computes_clv(connection) -> None:
    ledger = _ledger()
    ledger["settlements"] = {
        "cand1": {
            "state": "win", "settled_at": "2026-08-25T20:00:00Z",
            "result": {"home_goals_90": 2, "away_goals_90": 0},
        }
    }
    prospective = {"fixtures": {"fx1": {"closing": {"odds": 1.90}}}}
    assert journal_settlements(connection, ledger, prospective) == 1
    row = connection.execute("SELECT * FROM settlement").fetchone()
    assert row["state"] == "WIN"
    assert row["final_score"] == "2:0"
    assert row["entry_odds"] == pytest.approx(2.10)
    assert row["fair_at_entry"] == pytest.approx(1 / 0.55)
    # CLV = (2.10 / 1.90 - 1) * 100
    assert row["clv"] == pytest.approx((2.10 / 1.90 - 1) * 100)


def test_settlement_without_a_closing_price_has_no_clv(connection) -> None:
    ledger = _ledger()
    ledger["settlements"] = {"cand1": {"state": "loss", "settled_at": "2026-08-25T20:00:00Z"}}
    journal_settlements(connection, ledger, {"fixtures": {}})
    row = connection.execute("SELECT clv, fair_at_entry FROM settlement").fetchone()
    assert row["clv"] is None
    assert row["fair_at_entry"] == pytest.approx(1 / 0.55)


def test_calibration_buckets_use_the_entry_belief(connection) -> None:
    journal_positions(connection, _ledger())
    ledger = _ledger()
    ledger["settlements"] = {"cand1": {"state": "win", "settled_at": "2026-08-25T20:00:00Z"}}
    journal_settlements(connection, ledger, None)
    buckets = {row["bucket"]: row for row in calibration_buckets(connection)}
    assert buckets["50-60"]["n"] == 1
    assert buckets["50-60"]["predicted"] == pytest.approx(0.55)
    assert buckets["50-60"]["observed"] == 1.0
