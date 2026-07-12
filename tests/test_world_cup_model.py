import json
from copy import deepcopy

import numpy as np
import pytest

from xgedge.international.fifa import (
    load_fifa_fixtures,
    load_fifa_rankings,
    normalize_fixtures,
    normalize_rankings,
)
from xgedge.international.model import WorldCupModel


def _rankings():
    teams = [
        ("A", "Alpha", 1850.0, 1),
        ("B", "Beta", 1750.0, 5),
        ("C", "Gamma", 1650.0, 12),
        ("D", "Delta", 1550.0, 25),
    ]
    return {
        "publication_utc": "2026-06-10T12:00:00Z",
        "rankings": [
            {"team_id": key, "team": name, "rating": rating, "rank": rank}
            for key, name, rating, rank in teams
        ],
    }


def _matches():
    return [
        {
            "id": "m1", "kickoff_utc": "2026-06-11T19:00:00Z",
            "home_id": "A", "home": "Alpha", "away_id": "D", "away": "Delta",
            "stage": "Group", "status": "FINISHED", "home_goals_90": 2, "away_goals_90": 0,
        },
        {
            "id": "m2", "kickoff_utc": "2026-06-12T19:00:00Z",
            "home_id": "B", "home": "Beta", "away_id": "C", "away": "Gamma",
            "stage": "Group", "status": "FINISHED", "home_goals_90": 1, "away_goals_90": 1,
        },
        {
            "id": "m3", "kickoff_utc": "2026-06-20T19:00:00Z",
            "home_id": "A", "home": "Alpha", "away_id": "C", "away": "Gamma",
            "stage": "Group", "status": "FINISHED", "home_goals_90": 1, "away_goals_90": 0,
        },
        {
            "id": "future-result", "kickoff_utc": "2026-07-13T18:00:00Z",
            "home_id": "A", "home": "Alpha", "away_id": "D", "away": "Delta",
            "stage": "Quarter-final", "status": "FINISHED", "home_goals_90": 0, "away_goals_90": 12,
        },
        {
            "id": "sf1", "kickoff_utc": "2026-07-14T19:00:00Z",
            "home_id": "A", "home": "Alpha", "away_id": "B", "away": "Beta",
            "stage": "Semi-final", "status": "SCHEDULED",
        },
        {
            "id": "sf2", "kickoff_utc": "2026-07-15T19:00:00Z",
            "home_id": "C", "home": "Gamma", "away_id": "D", "away": "Delta",
            "stage": "Semi-final", "status": "SCHEDULED",
        },
    ]


def test_pre_tournament_ranking_is_required():
    payload = _rankings()
    payload["publication_utc"] = "2026-06-12T00:00:00Z"
    with pytest.raises(ValueError, match="after the tournament"):
        normalize_rankings(payload)


def test_offline_json_roundtrip(tmp_path):
    rankings_path = tmp_path / "rankings.json"
    fixtures_path = tmp_path / "fixtures.json"
    rankings_path.write_text(json.dumps(_rankings()), encoding="utf-8")
    fixtures_path.write_text(json.dumps({"matches": _matches()}), encoding="utf-8")
    ranking_snapshot = load_fifa_rankings(rankings_path)
    fixture_snapshot = load_fifa_fixtures(fixtures_path)
    assert ranking_snapshot["publication_utc"] == "2026-06-10T12:00:00Z"
    assert len(fixture_snapshot["matches"]) == 6


def test_timeline_recovers_regulation_score_not_extra_time_score():
    raw = {
        "calendar": {
            "Results": [
                {
                    "IdMatch": "et1", "Date": "2026-07-01T19:00:00Z",
                    "Home": {"IdTeam": "A", "ShortClubName": "Alpha"},
                    "Away": {"IdTeam": "B", "ShortClubName": "Beta"},
                    "MatchStatus": 0, "ResultType": 3,
                    "HomeTeamScore": 3, "AwayTeamScore": 1,
                    "StageName": [{"Locale": "en-GB", "Description": "Round of 16"}],
                }
            ]
        },
        "timelines": {
            "et1": {
                "Event": [
                    {"Period": 3, "HomeGoals": 1, "AwayGoals": 0},
                    {"Period": 5, "HomeGoals": 1, "AwayGoals": 1},
                    {"Period": 7, "HomeGoals": 2, "AwayGoals": 1},
                    {"Period": 9, "HomeGoals": 3, "AwayGoals": 1},
                ]
            }
        },
    }
    match = normalize_fixtures(raw)["matches"][0]
    assert (match["home_goals_90"], match["away_goals_90"]) == (1, 1)


def test_probabilities_are_valid_and_reproducible():
    model = WorldCupModel(_rankings(), _matches(), uncertainty_draws=100, random_seed=7)
    fixture = next(row for row in _matches() if row["id"] == "sf1")
    first = model.predict(fixture, as_of="2026-07-13T00:00:00Z")
    second = model.predict(fixture, as_of="2026-07-13T00:00:00Z")
    probabilities = first["probabilities"]
    assert first == second
    assert np.isclose(probabilities["home"] + probabilities["draw"] + probabilities["away"], 1.0)
    assert np.isclose(probabilities["over_2_5"] + probabilities["under_2_5"], 1.0)
    assert np.isclose(probabilities["btts_yes"] + probabilities["btts_no"], 1.0)
    assert all(0.0 <= value <= 1.0 for value in probabilities.values())
    assert first["label"] == "experimental"
    assert "not a betting recommendation" in first["warning"]
    assert first["scope"].startswith("90_minutes")


def test_no_leak_from_result_after_as_of_or_scheduled_scores():
    rows = _matches()
    target = next(row for row in rows if row["id"] == "sf1")
    clean = [row for row in rows if row["id"] != "future-result"]
    polluted = deepcopy(rows)
    scheduled = next(row for row in polluted if row["id"] == "sf2")
    scheduled["home_goals_90"] = 50
    scheduled["away_goals_90"] = 0
    first = WorldCupModel(_rankings(), clean, uncertainty_draws=100).predict(
        target, as_of="2026-07-13T00:00:00Z"
    )
    second = WorldCupModel(_rankings(), polluted, uncertainty_draws=100).predict(
        target, as_of="2026-07-13T00:00:00Z"
    )
    assert first == second
    assert first["data_provenance"]["training_matches"] == 3


def test_prediction_rejects_cutoff_at_or_after_kickoff():
    fixture = next(row for row in _matches() if row["id"] == "sf1")
    model = WorldCupModel(_rankings(), _matches(), uncertainty_draws=100)
    with pytest.raises(ValueError, match="earlier"):
        model.predict(fixture, as_of=fixture["kickoff_utc"])


def test_upcoming_predicts_known_teams_without_stage_hardcoding():
    model = WorldCupModel(_rankings(), _matches(), uncertainty_draws=100)
    predictions = model.predict_upcoming(as_of="2026-07-13T00:00:00Z")
    assert [row["fixture_id"] for row in predictions] == ["sf1", "sf2"]


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self, calendar, timeline):
        self.calendar = calendar
        self.timeline = timeline
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "timelines" in url:
            return _Response(self.timeline)
        return _Response(self.calendar)


def test_live_fixture_network_is_mocked_and_timeline_is_requested():
    calendar = {
        "Results": [
            {
                "IdMatch": "et1", "Date": "2026-07-01T19:00:00Z",
                "Home": {"IdTeam": "A", "ShortClubName": "Alpha"},
                "Away": {"IdTeam": "B", "ShortClubName": "Beta"},
                "MatchStatus": 0, "ResultType": 2,
                "HomeTeamScore": 5, "AwayTeamScore": 4,
            }
        ]
    }
    timeline = {"Event": [{"Period": 5, "HomeGoals": 0, "AwayGoals": 0}]}
    session = _Session(calendar, timeline)
    result = load_fifa_fixtures(session=session)
    assert result["matches"][0]["home_goals_90"] == 0
    assert len(session.calls) == 2
    assert session.calls[0][1]["timeout"] == 30.0


def test_live_ranking_network_is_mocked():
    payload = {
        "Results": [
            {
                "IdTeam": "A",
                "TeamName": [{"Locale": "en-GB", "Description": "Alpha"}],
                "IdCountry": "ALP",
                "Rank": 1,
                "DecimalTotalPoints": 1850.25,
                "PubDate": "2026-06-11T10:00:00Z",
            }
        ]
    }
    session = _Session(payload, {})
    result = load_fifa_rankings(session=session)
    assert result["rankings"][0]["rating"] == 1850.25
    assert len(session.calls) == 1
    assert session.calls[0][1]["params"]["gender"] == 1
