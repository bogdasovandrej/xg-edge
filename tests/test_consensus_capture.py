"""The consensus needs its own multi-book snapshot to be worth anything."""
from __future__ import annotations

import json

import pytest

from scripts.capture_consensus_odds import capture
from xgedge.decision.market_consensus import evaluate_market


def _odds_api_event(event_id: str, books: int) -> dict:
    """One The Odds API event carrying ``books`` bookmakers."""
    return {
        "id": event_id,
        "sport_key": "soccer_uefa_champs_league",
        "commence_time": "2026-08-25T18:00:00Z",
        "home_team": "Home", "away_team": "Away",
        "bookmakers": [
            {
                "key": f"book{index}", "title": f"Book {index}",
                "last_update": "2026-08-24T12:00:00Z",
                "markets": [{
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Home", "price": 2.05 + index * 0.05},
                        {"name": "Draw", "price": 3.40},
                        {"name": "Away", "price": 3.75},
                    ],
                }],
            }
            for index in range(books)
        ],
    }


class _StubResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _StubSession:
    """Returns one multi-book event for any request."""

    def __init__(self, books: int):
        self.books = books
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return _StubResponse([_odds_api_event("evt1", self.books)])


def test_capture_makes_no_request_without_a_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    from scripts import capture_consensus_odds

    capture_consensus_odds.main([
        "--fixtures", str(tmp_path / "absent.json"),
        "--output", str(tmp_path / "out.json"),
    ])
    assert "not configured" in capsys.readouterr().out
    assert not (tmp_path / "out.json").exists()


def test_two_books_cannot_support_a_consensus() -> None:
    """Why the extra capture exists at all: the primary plan returns two."""
    books = {
        "Bet365": {"home": 2.05, "draw": 3.40, "away": 3.75},
        "Unibet": {"home": 2.10, "draw": 3.35, "away": 3.70},
    }
    assert evaluate_market(books)["status"] == "INSUFFICIENT_BOOKS"


def test_six_books_do_support_a_consensus() -> None:
    books = {
        f"Book {index}": {"home": 2.05 + index * 0.02, "draw": 3.40, "away": 3.75}
        for index in range(6)
    }
    result = evaluate_market(books)
    assert result["status"] == "OK"
    assert result["books"] == 6
    # Each book is judged against the other five, never against itself.
    assert all(row["consensus_books"] == 5 for row in result["candidates"])


def test_capture_reports_how_many_records_can_be_judged(monkeypatch) -> None:
    import xgedge.data.bookmaker_odds as odds_module

    session = _StubSession(books=5)
    monkeypatch.setattr(odds_module.requests, "Session", lambda: session)
    snapshot = capture(
        api_key="test-key",
        fixtures=[{
            "id": "evt1", "competition": "UEFA Champions League",
            "home": "Home", "away": "Away",
            "kickoff_utc": "2026-08-25T18:00:00Z",
        }],
        snapshot_at="2026-08-24T12:00:00Z",
    )
    summary = snapshot["consensus_capture"]
    assert summary["purpose"] == "leave_one_out_bookmaker_consensus_only"
    # The capture must never leak into the frozen CLV protocol.
    assert "prospective_clv_ledger" in summary["excluded_from"]
    assert "paper_candidate_ranking" in summary["excluded_from"]
    assert isinstance(summary["records_with_three_or_more_books"], int)
