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


def test_live_payload_uses_verified_market_anchor() -> None:
    world_cup = {"predictions": [{
        "fixture_id": "wc2", "stage": "Semi-final", "kickoff_utc": "2026-07-15T19:00:00Z",
        "home": "England", "away": "Argentina", "model": "fifa_model",
        "probabilities": {"home": .3221, "draw": .2576, "away": .4203, "over_2_5": .57, "btts_yes": .60},
        "top_scores": [{"score": "1-1", "probability": .12}],
        "uncertainty": {"p_home": [.20, .48]},
    }]}
    market = {"snapshots": [{
        "fixture_id": "wc2", "kickoff_utc": "2026-07-15T19:00:00Z",
        "captured_at_utc": "2026-07-13T00:30:43Z", "market": "regulation_1x2",
        "bookmaker": "test", "odds_american": {"home": 155, "draw": 200, "away": 205},
    }]}
    audit = {"selected_anchor": {
        "config": {"residual_weight": .15, "longshot_weight": 0, "longshot_probability": .15,
                   "edge_threshold": .03, "max_odds": 6, "bias_l2": .1},
        "bias": [0, 0, 0],
    }}

    payload = build_payload(
        world_cup, {"predictions": []}, [{"id": "wc2"}], "2026-07-13T01:00:00Z",
        market_document=market, anchor_audit=audit,
    )
    row = payload["forecasts"][0]

    assert row["probability_basis"] == "market_anchored"
    assert row["p_home"] > row["p_away"]
    assert row["raw_model_1x2"]["away"] > row["raw_model_1x2"]["home"]
    assert row["details"]["betting_gate"]["allowed"] is False
    assert len(row["details"]["candidate_bets"]) == 3


def test_live_payload_builds_dynamic_elo_dossier_from_official_history() -> None:
    world_cup = {"predictions": [{
        "fixture_id": "future", "stage": "Semi-final", "kickoff_utc": "2026-07-15T19:00:00Z",
        "home": "England", "away": "Argentina", "model": "fifa_model",
        "probabilities": {"home": .35, "draw": .30, "away": .35, "over_2_5": .5, "btts_yes": .5},
        "top_scores": [], "uncertainty": {"p_home": [.2, .5]},
    }]}
    fixture = {
        "id": "future", "source": "fifa", "kickoff_utc": "2026-07-15T19:00:00Z",
        "home_id": "eng", "away_id": "arg", "home": "England", "away": "Argentina",
        "referee": None,
    }
    history = {"matches": [{
        "id": "past", "kickoff_utc": "2026-07-01T12:00:00Z", "status": "FINISHED",
        "home_id": "eng", "away_id": "arg", "home": "England", "away": "Argentina",
        "home_goals_90": 2, "away_goals_90": 0,
    }]}
    rankings = {"rankings": [
        {"team_id": "eng", "rating": 1800}, {"team_id": "arg", "rating": 1850},
    ]}

    payload = build_payload(
        world_cup, {"predictions": []}, [fixture], "2026-07-13T00:00:00Z",
        world_cup_history=history, rankings=rankings,
    )
    details = payload["forecasts"][0]["details"]

    assert details["teams"]["home"]["elo"] > 1800
    assert details["teams"]["away"]["elo"] < 1850
    assert details["teams"]["home"]["recent_matches"][0]["match_id"] == "past"
    assert details["data_quality"]["warnings"]
    assert details["betting_gate"]["allowed"] is False
