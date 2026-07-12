from __future__ import annotations

import pytest

from scripts.build_live_payload import build_payload


def test_live_payload_combines_models_and_forces_no_bet() -> None:
    world_cup = {"predictions": [{
        "fixture_id": "wc1", "stage": "Semi-final", "kickoff_utc": "2026-07-14T19:00:00Z",
        "home": "France", "away": "Spain", "model": "fifa_model",
        "probabilities": {"home": .37, "draw": .31, "away": .32, "over_2_5": .4, "btts_yes": .45},
        "top_scores": [{"score": "1-1", "probability": .14}],
        "uncertainty": {"p_home": [.2, .55]},
    }]}
    ucl = {"predictions": [{
        "fixture_id": "ucl1", "kickoff_utc": "2026-07-14T15:00:00Z",
        "home": "KuPS", "away": "Vardar", "status": "ok",
        "expected_goals_90m": {"home": 2.0, "away": .5},
        "probabilities_90m": {"home_win": .74, "draw": .18, "away_win": .08},
        "uncertainty_90m": {"intervals": {"home_win": {"low": .68, "high": .79}}},
        "most_likely_scores_90m": [{"score": "2-0", "probability": .16}],
    }]}
    fixtures = [
        {"id": "wc1", "venue": "Dallas Stadium"},
        {"id": "ucl1", "venue": "Kuopio", "aggregate_home_score": 2, "aggregate_away_score": 0},
    ]

    payload = build_payload(world_cup, ucl, fixtures, "2026-07-13T00:00:00Z")

    assert payload["betting_gate"]["allowed"] is False
    assert [row["id"] for row in payload["forecasts"]] == ["ucl1", "wc1"]
    assert all(row["recommendation"] == "NO BET" for row in payload["forecasts"])
    ucl_row = payload["forecasts"][0]
    assert ucl_row["first_leg"] == "Агрегат 2:0"
    assert ucl_row["p_home_advance"] is None
    assert ucl_row["p_over25"] == pytest.approx(0.4561869)
    assert payload["forecasts"][1]["home"] == "Франция"
