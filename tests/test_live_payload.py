from __future__ import annotations

import pytest

from scripts.build_live_payload import build_payload
from xgedge.simulation.ledger import new_paper_ledger


def test_live_payload_combines_models_and_publishes_full_line() -> None:
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
        "expected_goals_basis": {
            "method": "official_uefa_recent_totals_bayesian_shrinkage",
            "expected_total_goals": 2.5,
            "prior_total_goals": 2.65,
            "prior_matches": 5,
            "recent_match_limit": 10,
            "team_histories_used": [{"side": "home", "matches": 10}],
        },
        "ratings": {
            "home": {"elo": 1600, "source": "clubelo", "matches": None},
            "away": {
                "elo": 1512,
                "source": "uefa_official_results",
                "matches": 4,
            },
            "basis": "clubelo_with_point_in_time_uefa_fallback",
        },
        "probabilities_90m": {"home_win": .74, "draw": .18, "away_win": .08},
        "uncertainty_90m": {"intervals": {"home_win": {"low": .68, "high": .79}}},
        "most_likely_scores_90m": [{"score": "2-0", "probability": .16}],
    }]}
    fixtures = [
        {"id": "wc1", "venue": "Dallas Stadium"},
        {"id": "ucl1", "venue": "Kuopio", "aggregate_home_score": 2, "aggregate_away_score": 0},
    ]

    payload = build_payload(
        world_cup,
        ucl,
        fixtures,
        "2026-07-13T00:00:00Z",
        paper_ledger=new_paper_ledger(created_at="2026-07-13T00:00:00Z"),
    )

    assert payload["betting_gate"]["allowed"] is False
    assert payload["status"] == "MODEL_FORECASTS_ACTIVE_CLV_BACKGROUND_AUDIT"
    assert payload["validation_protocol"]["real_money_execution"] is False
    assert payload["paper_candidate_ranking"]["status"] == "PAPER_ONLY"
    assert payload["paper_candidate_ranking"]["candidates"] == []
    assert payload["paper_trading"]["starting_balance_rub"] == 10_000
    assert payload["paper_trading"]["totals"]["strategies"] == 3
    assert [row["id"] for row in payload["forecasts"]] == ["ucl1", "wc1"]
    assert all(row["recommendation"] == "MODEL FORECAST" for row in payload["forecasts"])
    assert all(
        row["decision_status"] == "MODEL_FORECAST_AVAILABLE"
        for row in payload["forecasts"]
    )
    assert all(row["market_period"] == "REGULATION_90_MINUTES" for row in payload["forecasts"])
    assert all(
        row["forecast_generated_at"] == "2026-07-13T00:00:00Z"
        for row in payload["forecasts"]
    )
    ucl_row = payload["forecasts"][0]
    assert ucl_row["first_leg"] == "Агрегат 2:0"
    assert ucl_row["p_home_advance"] is None
    assert ucl_row["p_over25"] == pytest.approx(0.4561869)
    assert ucl_row["p_over35"] == pytest.approx(0.2424176)
    assert ucl_row["p_over45"] == pytest.approx(0.1088146)
    assert ucl_row["expected_goals"]["total"] == pytest.approx(2.5)
    assert ucl_row["expected_goals_basis"]["expected_total_goals"] == 2.5
    assert ucl_row["probability_basis"] == (
        "clubelo_with_point_in_time_uefa_fallback"
    )
    assert ucl_row["rating_basis"]["away"]["source"] == "uefa_official_results"
    assert ucl_row["top_score_probability"] == .16
    assert ucl_row["score_display"] == "distribution_not_exact_score_prediction"
    assert ucl_row["tail_probability_status"] == "RAW_POISSON_UNCALIBRATED_NO_BET"
    assert len(ucl_row["model_market_forecasts"]) == 40
    assert {
        row["market"] for row in ucl_row["model_market_forecasts"]
    } == {
        "1x2", "double_chance", "draw_no_bet", "btts", "totals",
        "team_totals", "asian_handicap",
    }
    assert all(
        0 < row["conservative_probability"] < row["theoretical_probability"] < 1
        for row in ucl_row["model_market_forecasts"]
    )
    recommended = [
        row for row in ucl_row["model_market_forecasts"]
        if row["recommendation_rank"] is not None
    ]
    assert sorted(row["recommendation_rank"] for row in recommended) == [1, 2, 3]
    assert len({row["recommendation_group"] for row in recommended}) == 3
    assert [
        row["recommendation_role"]
        for row in sorted(recommended, key=lambda row: row["recommendation_rank"])
    ] == ["VALUE_SINGLE", "BALANCED_SINGLE", "EXPRESS_LEG"]
    assert [
        row["target_market_odds"]
        for row in sorted(recommended, key=lambda row: row["recommendation_rank"])
    ] == [1.8, 1.5, 1.3]
    value_single = next(
        row for row in recommended if row["recommendation_role"] == "VALUE_SINGLE"
    )
    assert value_single["minimum_market_odds"] > 1.5
    assert all(
        row["minimum_market_odds"] >= row["conservative_fair_odds"]
        for row in recommended
    )
    assert all(row["price_status"] == "AWAITING_BOOKMAKER_PRICE" for row in recommended)
    assert all(row["reliability_haircut"] == pytest.approx(.03) for row in recommended)
    assert all(
        row["conservative_probability"]
        == pytest.approx(row["theoretical_probability"] - .03)
        for row in recommended
    )
    assert payload["forecasts"][1]["home"] == "Франция"
    assert payload["forecasts"][1]["score_scenarios_coverage"] == .14


def test_live_payload_includes_top_five_calendar_without_fake_probabilities() -> None:
    payload = build_payload(
        {"predictions": []},
        {"predictions": []},
        [],
        "2026-08-01T00:00:00Z",
        top_five_fixtures={
            "schema_version": "top-five-fixtures/1.0",
            "generated_at": "2026-08-01T00:00:00Z",
            "fixtures": [{
                "id": "fdorg:PL:100",
                "competition": "Premier League",
                "round": "Matchday 1",
                "kickoff_utc": "2026-08-15T16:30:00Z",
                "home": "Man City",
                "away": "Arsenal",
                "venue": None,
            }],
        },
    )

    row = payload["forecasts"][0]
    assert row["id"] == "fdorg:PL:100"
    assert row["competition"] == "Premier League"
    assert row["p_home"] is None
    assert row["betting_eligible"] is False
    assert row["probability_basis"] == "calendar_only_no_validated_top5_features"


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


def test_live_payload_drops_past_fixtures_and_uses_official_uefa_round_and_leg() -> None:
    predictions = {
        "predictions": [
            {
                "fixture_id": "past",
                "kickoff_utc": "2026-07-20T18:00:00Z",
                "home": "Past Home",
                "away": "Past Away",
                "status": "ok",
                "expected_goals_90m": {"home": 1.0, "away": 1.0},
                "probabilities_90m": {"home_win": .35, "draw": .30, "away_win": .35},
            },
            {
                "fixture_id": "future",
                "kickoff_utc": "2026-07-22T18:00:00Z",
                "home": "Future Home",
                "away": "Future Away",
                "status": "ok",
                "expected_goals_90m": {"home": 1.2, "away": .8},
                "probabilities_90m": {"home_win": .45, "draw": .30, "away_win": .25},
            },
        ]
    }
    fixtures = [
        {
            "id": "past",
            "source": "uefa",
            "competition_id": "1",
            "competition": "UEFA Champions League",
            "kickoff_utc": "2026-07-20T18:00:00Z",
            "home_id": "past-home",
            "away_id": "past-away",
            "home": "Past Home",
            "away": "Past Away",
            "round": "First qualifying round",
            "leg": 2,
        },
        {
            "id": "future",
            "competition": "UEFA Champions League",
            "round": "Second qualifying round",
            "leg": 1,
        },
    ]

    payload = build_payload(
        {"predictions": []}, predictions, fixtures, "2026-07-21T00:00:00Z"
    )

    assert [row["id"] for row in payload["forecasts"]] == ["future"]
    assert payload["forecasts"][0]["stage"] == (
        "2-й квалификационный раунд · первый матч"
    )


def test_live_payload_uses_verified_uefa_history_without_inventing_xg() -> None:
    uefa = {"predictions": [{
        "fixture_id": "future", "kickoff_utc": "2026-07-22T18:00:00Z",
        "home": "Home", "away": "Away", "status": "ok",
        "ratings": {"home": {"elo": 1550}, "away": {"elo": 1500}},
        "expected_goals_90m": {"home": 1.2, "away": .8},
        "probabilities_90m": {"home_win": .45, "draw": .30, "away_win": .25},
    }]}
    fixture = {
        "id": "future", "source": "uefa", "competition_id": "1",
        "competition": "UEFA Champions League", "kickoff_utc": "2026-07-22T18:00:00Z",
        "home_id": "h", "away_id": "a", "home": "Home", "away": "Away",
    }
    history = {
        "schema_version": "uefa-club-history/1.0",
        "matches": [{
            "id": "past", "kickoff_utc": "2026-07-01T18:00:00Z",
            "home_id": "h", "away_id": "old", "home": "Home", "away": "Old",
            "home_goals_90": 2, "away_goals_90": 0, "status": "FINISHED",
            "official": True, "scope": "club", "competition": "UEFA Champions League",
            "competition_level": "uefa_champions_league",
            "provenance": {"source": "official_uefa_match_api", "xg": "not_provided"},
        }],
    }

    payload = build_payload(
        {"predictions": []}, uefa, [fixture], "2026-07-21T00:00:00Z",
        uefa_history=history,
    )
    recent = payload["forecasts"][0]["details"]["teams"]["home"]["recent_matches"]

    assert recent[0]["match_id"] == "past"
    assert recent[0]["score_90"] == {"for": 2, "against": 0}
    assert recent[0]["xg"]["non_penalty"]["status"] == "unknown"


def test_uefa_prediction_keeps_competition_when_fixture_snapshot_is_missing() -> None:
    payload = build_payload(
        {"predictions": []},
        {"predictions": [{
            "fixture_id": "uel-future",
            "kickoff_utc": "2026-07-23T18:00:00Z",
            "competition_id": "14",
            "competition": "UEFA Europa League",
            "round": "Second qualifying round",
            "stage": "QUALIFYING",
            "leg": 1,
            "home": "Home",
            "away": "Away",
            "status": "ok",
            "expected_goals_90m": {"home": 1.3, "away": 0.9},
            "probabilities_90m": {
                "home_win": 0.46,
                "draw": 0.29,
                "away_win": 0.25,
            },
        }]},
        [],
        "2026-07-23T12:00:00Z",
    )

    assert payload["forecasts"][0]["competition"] == "UEFA Europa League"
    assert len(payload["forecasts"][0]["model_market_forecasts"]) == 40
