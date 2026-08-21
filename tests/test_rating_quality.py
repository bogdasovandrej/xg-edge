"""A collapsed rating ladder must not be allowed to price bets."""
from __future__ import annotations

import pytest

from xgedge.decision.ranking import rank_paper_candidates
from xgedge.experiments.rating_quality import (
    RatingQualityPolicy,
    assess_rating_quality,
)


def _prediction(home_elo: float, away_elo: float, source: str) -> dict:
    return {
        "ratings": {
            "home": {"elo": home_elo, "source": source},
            "away": {"elo": away_elo, "source": source},
        }
    }


def test_real_clubelo_ladder_is_active() -> None:
    predictions = [
        _prediction(1780, 1450, "clubelo"),
        _prediction(1900, 1300, "clubelo"),
        _prediction(1650, 1520, "clubelo"),
    ]
    quality = assess_rating_quality(predictions)
    assert quality["status"] == "ACTIVE"
    assert quality["betting_eligible"] is True
    assert quality["reasons"] == []


def test_the_august_2026_collapse_is_caught() -> None:
    """The real incident: 86 teams from the fallback spanning 1420-1624."""
    predictions = [
        _prediction(1623.8, 1420.0, "uefa_official_results"),
        _prediction(1550.1, 1429.1, "uefa_official_results"),
        _prediction(1442.3, 1450.8, "uefa_official_results"),
    ]
    quality = assess_rating_quality(predictions)
    assert quality["status"] == "DEGRADED"
    assert quality["betting_eligible"] is False
    assert "insufficient_trusted_rating_coverage" in quality["reasons"]
    assert "collapsed_elo_ladder" in quality["reasons"]
    assert quality["trusted_share"] == 0.0


def test_wide_ladder_from_an_untrusted_source_is_still_refused() -> None:
    """Spread alone is not enough; the source has to be a real provider."""
    predictions = [
        _prediction(1900, 1300, "uefa_official_results"),
        _prediction(1850, 1250, "uefa_official_results"),
    ]
    quality = assess_rating_quality(predictions)
    assert quality["betting_eligible"] is False
    assert quality["reasons"] == ["insufficient_trusted_rating_coverage"]


def test_trusted_source_with_a_collapsed_ladder_is_also_refused() -> None:
    predictions = [
        _prediction(1510, 1495, "clubelo"),
        _prediction(1505, 1500, "clubelo"),
    ]
    quality = assess_rating_quality(predictions)
    assert quality["betting_eligible"] is False
    assert quality["reasons"] == ["collapsed_elo_ladder"]


def test_no_ratings_is_degraded_not_silently_fine() -> None:
    quality = assess_rating_quality([])
    assert quality["status"] == "DEGRADED"
    assert quality["reasons"] == ["no_ratings_available"]


def test_policy_rejects_impossible_thresholds() -> None:
    with pytest.raises(ValueError):
        RatingQualityPolicy(minimum_trusted_share=1.5).validate()
    with pytest.raises(ValueError):
        RatingQualityPolicy(minimum_elo_spread=0).validate()


def _payload_with(rating_quality: dict) -> dict:
    """A payload whose candidate would otherwise sail through the ranker."""
    return {
        "generated_at": "2026-08-20T12:00:00Z",
        "forecasts": [{
            "id": "fx1",
            "kickoff_utc": "2026-08-25T18:00:00Z",
            "forecast_generated_at": "2026-08-20T12:00:00Z",
            "uncertainty": "low",
            "rating_quality": rating_quality,
            "details": {
                "data_quality": {"score": 90},
                "market_snapshot": {
                    "status": "SHADOW_ONLY",
                    "captured_at_utc": "2026-08-20T12:30:00Z",
                    "bookmaker": "Unibet",
                },
                "market_candidates": [{
                    "market": "1x2", "outcome": "home", "selection": "П1", "line": None,
                    "probability": 0.58, "market_odds": 3.15,
                    "point_edge": 0.58 * 3.15 - 1.0,
                    "bookmaker": "Unibet", "bookmaker_key": "unibet",
                }],
            },
        }],
    }


def test_ranker_refuses_candidates_from_a_degraded_basis() -> None:
    degraded = {"betting_eligible": False, "reasons": ["collapsed_elo_ladder"]}
    result = rank_paper_candidates(_payload_with(degraded))
    assert result["candidates"] == []
    assert result["rejection_counts"].get("degraded_rating_basis") == 1


def test_ranker_still_accepts_the_same_candidate_on_a_healthy_basis() -> None:
    healthy = {"betting_eligible": True, "reasons": []}
    result = rank_paper_candidates(_payload_with(healthy))
    assert len(result["candidates"]) == 1
    assert "degraded_rating_basis" not in result["rejection_counts"]
