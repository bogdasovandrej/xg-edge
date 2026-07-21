from __future__ import annotations

import pytest

from xgedge.data.point_in_time import available_snapshot
from xgedge.dossier.adjustments import (
    adjusted_match_npxg,
    extract_non_penalty_xg,
    red_card_neutralization,
)
from xgedge.dossier.builder import build_match_dossier
from xgedge.dossier.elo import PointInTimeElo


def _match(
    match_id: str,
    kickoff: str,
    home: str,
    away: str,
    score: tuple[int, int],
    **extra,
) -> dict:
    return {
        "id": match_id,
        "kickoff_utc": kickoff,
        "home_id": home,
        "away_id": away,
        "home": home,
        "away": away,
        "home_goals_90": score[0],
        "away_goals_90": score[1],
        "status": "FINISHED",
        "official": True,
        "scope": "national",
        "neutral_venue": True,
        "competition": "Test Cup",
        "competition_level": "international_major",
        **extra,
    }


def test_point_in_time_elo_rewards_upset_and_is_zero_sum() -> None:
    match = _match("m1", "2026-01-01T12:00:00Z", "weak", "strong", (1, 0))
    priors = {("national", "weak"): 1400.0, ("national", "strong"): 1800.0}
    elo = PointInTimeElo([match], priors=priors)

    weak = elo.rating_at("weak", "national", "2026-01-02T00:00:00Z")
    strong = elo.rating_at("strong", "national", "2026-01-02T00:00:00Z")

    assert weak["rating"] > 1400
    assert strong["rating"] < 1800
    assert weak["rating"] + strong["rating"] == pytest.approx(3200)


def test_elo_ignores_non_official_and_prevents_same_time_leakage() -> None:
    first = _match("a", "2026-01-01T12:00:00Z", "A", "B", (1, 0))
    simultaneous = _match("b", "2026-01-01T12:00:00Z", "C", "D", (0, 1))
    friendly = _match("friendly", "2026-01-02T12:00:00Z", "A", "C", (9, 0))
    friendly["official"] = False
    elo = PointInTimeElo([first, simultaneous, friendly])

    assert elo.before_match("a")["home"]["rating"] == 1500
    assert elo.before_match("b")["home"]["rating"] == 1500
    assert elo.rating_at("A", "national", "2026-01-03T00:00:00Z")["matches"] == 1
    assert elo.ignored_records == [{"match_id": "friendly", "reason": "not_explicitly_official"}]


def test_npxg_does_not_guess_unknown_penalties() -> None:
    assert extract_non_penalty_xg({"xg_home": 1.8}, "home")["status"] == "unknown"
    result = extract_non_penalty_xg(
        {"xg_home": 1.8, "penalties_taken_home": 1}, "home"
    )
    assert result["value"] == pytest.approx(1.04)
    assert "standard_penalty_xg=0.76" in result["assumptions"]


def test_red_card_adjustment_uses_minute_side_and_score() -> None:
    early = [{
        "event_id": "r1", "minute": 20, "red_card_side": "away",
        "score_before_home": 0, "score_before_away": 0,
    }]
    late = [{
        "event_id": "r2", "minute": 85, "red_card_side": "away",
        "score_before_home": 0, "score_before_away": 0,
    }]

    early_adjusted = red_card_neutralization(2.0, "home", early)
    late_adjusted = red_card_neutralization(2.0, "home", late)

    assert early_adjusted["value"] < late_adjusted["value"] < 2.0
    assert early_adjusted["components"][0]["score_before"] == {"home": 0, "away": 0}


def test_adjusted_xg_rewards_strong_opposition_without_claiming_causality() -> None:
    row = {"npxg_home": 1.0, "npxg_home_source": "licensed", "red_cards": []}
    result = adjusted_match_npxg(row, "home", opponent_elo=1900)

    assert result["status"] == "available"
    assert result["value"] > 1.0
    assert result["red_card_adjustment"]["warning"] == "heuristic_not_causal_estimate"


def test_dossier_keeps_last_ten_official_and_rejects_future_context() -> None:
    history = [
        _match(
            f"m{i}", f"2026-01-{i + 1:02d}T12:00:00Z",
            "A" if i % 2 == 0 else "O", "O" if i % 2 == 0 else "A", (1, 0),
            npxg_home=1.1, npxg_away=.7, red_cards=[],
            provenance={"source": "test_official"},
        )
        for i in range(12)
    ]
    fixture = {
        "id": "future", "kickoff_utc": "2026-02-01T12:00:00Z",
        "home_id": "A", "away_id": "B", "home": "A", "away": "B",
        "scope": "national", "competition_level": "international_major",
    }
    lineups = available_snapshot(
        "official", [{"match_id": "future", "team_id": "A", "player_id": "1",
                      "player_name": "Player", "lineup_status": "starter",
                      "is_confirmed": True, "field_position": "MIDFIELDER",
                      "jersey_number": 8}],
        snapshot_at="2026-01-31T12:00:00Z",
    )
    coaches = available_snapshot(
        "official",
        [{
            "match_id": "future", "team_id": "A", "coach_id": "c1",
            "coach_name": "Coach A", "role": "head_coach",
        }],
        snapshot_at="2026-01-31T12:00:00Z",
    )
    dossier = build_match_dossier(
        fixture, history, cutoff="2026-01-31T12:00:00Z",
        contexts={"lineups": lineups, "coaches": coaches},
        forecast_probabilities={"home": .5, "draw": .3, "away": .2},
    )

    assert len(dossier["teams"]["home"]["recent_matches"]) == 10
    assert dossier["teams"]["home"]["likely_lineup"][0]["player_name"] == "Player"
    assert dossier["teams"]["home"]["likely_lineup"][0]["field_position"] == "MIDFIELDER"
    assert dossier["teams"]["home"]["coach"]["coach_name"] == "Coach A"
    assert dossier["context_availability"]["coaches"]["status"] == "available"
    assert dossier["betting_gate"]["allowed"] is False
    assert dossier["tail_risk"]["interpretation"].endswith("not_black_swan_prediction")
    assert "fewer_than_10_official_matches_for_at_least_one_team" in dossier["data_quality"]["warnings"]

    future_lineup = available_snapshot("bad", [], snapshot_at="2026-02-02T12:00:00Z")
    with pytest.raises(ValueError, match="post-kickoff"):
        build_match_dossier(
            fixture, history, cutoff="2026-01-31T12:00:00Z",
            contexts={"lineups": future_lineup},
        )
