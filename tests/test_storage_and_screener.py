"""Persistent price history, analysis versioning and line-movement screening."""
from __future__ import annotations

import pytest

from xgedge.decision.screener import (
    ScreenerConfig,
    classify_move,
    delta_pct,
    movement_top,
    screen_quote_history,
)
from xgedge.storage.db import (
    calibration_buckets,
    current_analysis,
    market_id,
    quote_history,
    record_analysis,
    record_quote,
    record_settlement,
    store,
    upsert_fixture,
    upsert_market,
)


@pytest.fixture()
def connection(tmp_path):
    with store(tmp_path / "test.db") as conn:
        upsert_fixture(conn, {
            "id": "fx1", "competition": "UCL", "home": "A", "away": "B",
            "kickoff_utc": "2026-08-25T18:00:00Z",
        })
        upsert_market(conn, {
            "fixture_id": "fx1", "family": "team_totals", "selection": "home_under",
            "line": 1.0, "calc_mode": "WPL_FULL",
        })
        yield conn


KEY = market_id("fx1", "team_totals", "home_under", 1.0)


def test_price_history_is_append_only(connection) -> None:
    record_quote(connection, market_key=KEY, bookmaker="bk", odds=2.10,
                 checked_at="2026-08-24T10:00:00Z")
    record_quote(connection, market_key=KEY, bookmaker="bk", odds=2.30,
                 checked_at="2026-08-24T22:00:00Z")
    history = quote_history(connection, KEY)
    assert [row["odds"] for row in history] == [2.10, 2.30]


def test_screener_needs_two_observations(connection) -> None:
    record_quote(connection, market_key=KEY, bookmaker="bk", odds=2.10,
                 checked_at="2026-08-24T10:00:00Z")
    assert screen_quote_history(quote_history(connection, KEY)) is None


def test_screener_computes_delta_from_stored_reference(connection) -> None:
    record_quote(connection, market_key=KEY, bookmaker="bk", odds=2.00,
                 checked_at="2026-08-24T10:00:00Z")
    record_quote(connection, market_key=KEY, bookmaker="bk", odds=2.20,
                 checked_at="2026-08-24T22:00:00Z")
    screened = screen_quote_history(quote_history(connection, KEY))
    assert screened["delta_pct"] == pytest.approx(10.0)
    assert screened["state"] == "LARGE_MOVE"
    assert screened["assessment"] == "NEEDS_MECHANISM"


def test_move_classes() -> None:
    assert classify_move(2.001, 2.0)["state"] == "FROZEN"
    assert classify_move(2.06, 2.0)["state"] == "DRIFT"
    assert classify_move(1.94, 2.0)["state"] == "STEAM"
    assert classify_move(2.14, 2.0)["state"] == "LARGE_MOVE"
    assert classify_move(1.84, 2.0)["state"] == "LARGE_MOVE"


def test_large_move_is_not_scored_good_or_bad() -> None:
    lengthened = classify_move(2.20, 2.0)
    shortened = classify_move(1.80, 2.0)
    assert lengthened["assessment"] == shortened["assessment"] == "NEEDS_MECHANISM"
    assert lengthened["requires_human_audit"] is True


def test_limit_signal_only_when_provider_reports_it() -> None:
    assert classify_move(2.0, 2.0, limit_reported=True)["state"] == "LIMIT_SIGNAL"
    assert classify_move(2.0, 2.0)["state"] == "FROZEN"


def test_movement_top_is_sorted_by_delta_not_value() -> None:
    rows = [
        {"market_id": "a", "state": "DRIFT", "delta_pct": 2.0, "value_pct": 90.0},
        {"market_id": "b", "state": "LARGE_MOVE", "delta_pct": 11.0, "value_pct": 1.0},
        {"market_id": "c", "state": "FROZEN", "delta_pct": 0.1, "value_pct": 99.0},
    ]
    top = movement_top(rows)
    assert [row["market_id"] for row in top["rows"]] == ["b", "a"]
    assert top["sorted_by"] == "delta_pct"


def test_delta_rejects_impossible_odds() -> None:
    with pytest.raises(ValueError):
        delta_pct(1.0, 2.0)


def test_analysis_is_versioned_not_overwritten(connection) -> None:
    first = record_analysis(
        connection, market_key=KEY, model_version="v1",
        central={"win": 0.5, "push": 0.1, "loss": 0.4},
        conservative={"win": 0.46, "push": 0.1, "loss": 0.44},
        created_at="2026-08-24T10:00:00Z",
    )
    second = record_analysis(
        connection, market_key=KEY, model_version="v2",
        central={"win": 0.55, "push": 0.1, "loss": 0.35},
        conservative={"win": 0.51, "push": 0.1, "loss": 0.39},
        created_at="2026-08-24T20:00:00Z", supersedes=first,
    )
    rows = connection.execute("SELECT id, superseded_by FROM analysis ORDER BY id").fetchall()
    assert [(row["id"], row["superseded_by"]) for row in rows] == [(first, second), (second, None)]
    assert current_analysis(connection, KEY)["model_version"] == "v2"


def test_analysis_rejects_states_that_do_not_sum_to_one(connection) -> None:
    with pytest.raises(ValueError):
        record_analysis(
            connection, market_key=KEY, model_version="bad",
            central={"win": 0.5, "push": 0.1, "loss": 0.5},
            conservative={"win": 0.5, "push": 0.1, "loss": 0.4},
            created_at="2026-08-24T10:00:00Z",
        )


def test_settlement_stores_fair_at_entry_and_computes_clv(connection) -> None:
    record_settlement(
        connection, market_key=KEY, state="WIN", settled_at="2026-08-25T20:00:00Z",
        final_score="1:0", entry_odds=2.20, fair_at_entry=2.05, closing_odds=2.00,
    )
    row = connection.execute("SELECT * FROM settlement").fetchone()
    assert row["fair_at_entry"] == 2.05
    assert row["clv"] == pytest.approx(10.0)


def test_calibration_buckets_are_actually_computed(connection) -> None:
    record_analysis(
        connection, market_key=KEY, model_version="v1",
        central={"win": 0.65, "push": 0.0, "loss": 0.35},
        conservative={"win": 0.65, "push": 0.0, "loss": 0.35},
        created_at="2026-08-24T10:00:00Z",
    )
    record_settlement(connection, market_key=KEY, state="WIN", settled_at="2026-08-25T20:00:00Z")
    buckets = {row["bucket"]: row for row in calibration_buckets(connection)}
    assert buckets["60-70"]["n"] == 1
    assert buckets["60-70"]["observed"] == 1.0
    assert buckets["40-50"]["n"] == 0
