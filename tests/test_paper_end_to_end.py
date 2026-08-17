"""Regression for exact recommendation → PAPER → result → balance."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from xgedge.decision.ranking import rank_paper_candidates
from xgedge.simulation.ledger import new_paper_ledger, update_paper_ledger


T0 = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)


def test_non_1x2_quarter_line_survives_the_complete_paper_path() -> None:
    forecast = {
        "id": "uefa-1",
        "competition": "UEFA Champions League",
        "stage": "Qualifying",
        "kickoff_utc": (T0 + timedelta(hours=2)).isoformat(),
        "forecast_generated_at": (T0 - timedelta(hours=1)).isoformat(),
        "home": "Home",
        "away": "Away",
        "uncertainty": "low",
        "market_period": "REGULATION_90_MINUTES",
        "details": {
            "data_quality": {"score": 90},
            "market_candidates": [],
            "expanded_market_candidates": [{
                "selection": "Over 2.75",
                "outcome": "over",
                "market": "totals",
                "line": 2.75,
                "probability": .60,
                "market_odds": 2.0,
                "point_edge": .20,
                "bookmaker": "Verified Book",
                "bookmaker_key": "verified",
                "source_provider": "manual_verified",
            }],
            "market_snapshot": {
                "status": "SHADOW_ONLY",
                "captured_at_utc": (T0 - timedelta(minutes=10)).isoformat(),
                "source_provider": "manual_verified",
            },
        },
    }
    live = {"generated_at": T0.isoformat(), "forecasts": [forecast]}
    ranking = rank_paper_candidates(live)
    assert len(ranking["candidates"]) == 1
    recommended = ranking["candidates"][0]
    assert (recommended["market"], recommended["line"]) == ("totals", 2.75)

    live["paper_candidate_ranking"] = ranking
    opened, operation = update_paper_ledger(
        new_paper_ledger(created_at=T0 - timedelta(hours=2)), live, now=T0
    )
    assert operation["enrolled"] == 1
    candidate_id = recommended["candidate_id"]
    assert opened["enrollments"][candidate_id]["market"] == "totals"

    settled, operation = update_paper_ledger(
        opened,
        {"paper_candidate_ranking": {**ranking, "candidates": []}},
        now=T0 + timedelta(hours=3),
        official_results={
            "uefa-1": {
                "status": "FINISHED", "home_goals_90": 2,
                "away_goals_90": 1, "outcome": "home",
            }
        },
    )
    assert operation["settled"] == 1
    assert settled["settlements"][candidate_id]["selection_result"] == "half_win"
    assert settled["paper_trading"]["markets"]["totals"] == {
        "enrolled": 1, "settled": 1, "open": 0,
    }
    for strategy in settled["paper_trading"]["leaderboard"]:
        assert strategy["half_wins"] == 1
        assert strategy["equity_balance_rub"] == 10_050.0
