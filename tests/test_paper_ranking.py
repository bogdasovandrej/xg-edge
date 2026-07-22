from copy import deepcopy

import pytest

from xgedge.decision.ranking import PaperRankingConfig, rank_paper_candidates


def _forecast(fixture_id: str, *, quality: float = 90, status: str = "SHADOW_ONLY") -> dict:
    return {
        "id": fixture_id,
        "competition": "UEFA Champions League",
        "stage": "Qualifying",
        "kickoff_utc": "2026-07-29T18:00:00Z",
        "home": f"Home {fixture_id}",
        "away": f"Away {fixture_id}",
        "uncertainty": "низкая",
        "market_period": "REGULATION_90_MINUTES",
        "details": {
            "data_quality": {"score": quality},
            "market_snapshot": {
                "status": status,
                "captured_at_utc": "2026-07-29T16:00:00Z",
            },
            "market_candidates": [
                {
                    "selection": "П1", "outcome": "home", "probability": .55,
                    "market_odds": 2.0, "point_edge": .10, "bookmaker": "Book A",
                    "bookmaker_key": "a", "source_provider": "the_odds_api",
                },
                {
                    "selection": "X", "outcome": "draw", "probability": .30,
                    "market_odds": 3.5, "point_edge": .05, "bookmaker": "Book B",
                    "bookmaker_key": "b", "source_provider": "the_odds_api",
                },
            ],
        },
    }


def test_ranks_one_strict_candidate_per_match_without_mutating_payload() -> None:
    payload = {
        "generated_at": "2026-07-29T15:00:00Z",
        "forecasts": [_forecast("m2"), _forecast("m1")],
    }
    original = deepcopy(payload)
    result = rank_paper_candidates(payload)

    assert payload == original
    assert result["status"] == "PAPER_ONLY"
    assert result["real_money_execution"] is False
    assert result["eligible_matches"] == 2
    assert [row["fixture_id"] for row in result["candidates"]] == ["m1", "m2"]
    assert all(row["selection"] == "П1" for row in result["candidates"])
    assert result["candidates"][0]["probability_edge"] == pytest.approx(.05)
    assert result["candidates"][0]["robust_edge"] < .10


def test_fails_closed_on_low_quality_stale_or_weak_candidates() -> None:
    low = _forecast("low", quality=59)
    stale = _forecast("stale", status="STALE")
    weak = _forecast("weak")
    weak["details"]["market_candidates"][0]["point_edge"] = .02
    weak["details"]["market_candidates"][1]["point_edge"] = .01
    result = rank_paper_candidates({
        "generated_at": "2026-07-29T15:00:00Z",
        "forecasts": [low, stale, weak],
    })

    assert result["candidates"] == []
    assert result["rejection_counts"] == {
        "data_quality_below_threshold": 1,
        "market_snapshot_not_eligible": 1,
        "no_candidate_survived_strict_filter": 1,
    }


def test_config_validation_is_strict() -> None:
    with pytest.raises(ValueError, match="maximum_odds"):
        rank_paper_candidates(
            {"forecasts": []}, PaperRankingConfig(maximum_odds=1.0)
        )
    with pytest.raises(ValueError, match="forecasts"):
        rank_paper_candidates({})


def test_rejects_past_fixture_and_quote_outside_forecast_window() -> None:
    past = _forecast("past")
    past["kickoff_utc"] = "2026-07-29T14:00:00Z"
    early = _forecast("early")
    early["details"]["market_snapshot"]["captured_at_utc"] = (
        "2026-07-29T14:59:59Z"
    )

    result = rank_paper_candidates({
        "generated_at": "2026-07-29T15:00:00Z",
        "forecasts": [past, early],
    })

    assert result["candidates"] == []
    assert result["rejection_counts"] == {
        "fixture_not_future": 1,
        "quote_outside_forecast_window": 1,
    }


def test_quote_can_precede_payload_refresh_but_not_frozen_forecast() -> None:
    row = _forecast("context-refresh")
    row["forecast_generated_at"] = "2026-07-29T14:00:00Z"
    result = rank_paper_candidates({
        "generated_at": "2026-07-29T17:00:00Z",
        "forecasts": [row],
    })

    assert result["displayed_candidates"] == 1


def test_expanded_total_can_become_the_single_paper_candidate() -> None:
    row = _forecast("total")
    row["details"]["market_candidates"] = []
    row["details"]["expanded_market_candidates"] = [{
        "selection": "ТБ 2.5",
        "outcome": "over",
        "market": "totals",
        "line": 2.5,
        "probability": .57,
        "market_odds": 2.0,
        "point_edge": .14,
        "bookmaker": "Book A",
        "bookmaker_key": "a",
        "source_provider": "odds_api_io",
    }]
    result = rank_paper_candidates({
        "generated_at": "2026-07-29T15:00:00Z",
        "forecasts": [row],
    })

    assert result["displayed_candidates"] == 1
    candidate = result["candidates"][0]
    assert candidate["market"] == "totals"
    assert candidate["line"] == 2.5
    assert candidate["outcome"] == "over"
