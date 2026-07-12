from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pytest

from scripts.predict_ucl_qualifying import main as cli_main
from xgedge.experiments.ucl_qualifying import (
    ClubEloIndex,
    ClubEloRating,
    EloPoissonCalibration,
    clubelo_ranking_url,
    coverage_summary,
    fetch_clubelo_ratings,
    normalize_team_name,
    parse_clubelo_csv,
    predict_fixture,
)


AS_OF = datetime(2026, 7, 13, tzinfo=timezone.utc)


def _fixture(**overrides):
    fixture = {
        "id": "ucl-1",
        "kickoff_utc": "2026-07-14T18:00:00Z",
        "competition": "UEFA Champions League",
        "round": "First qualifying round",
        "leg": 1,
        "home": "Home FC",
        "away": "Away FK",
        "aggregate_home_score": None,
        "aggregate_away_score": None,
    }
    fixture.update(overrides)
    return fixture


def _ratings():
    return ClubEloIndex(
        [
            ClubEloRating("Home", "AAA", 1600.0),
            ClubEloRating("Away", "BBB", 1500.0),
        ]
    )


def test_name_normalization_and_explicit_aliases():
    assert normalize_team_name("  Győri ETO FC ") == "gyori eto"
    index = ClubEloIndex(
        [ClubEloRating("Gyoer", "HUN", 1450.0)],
        {"Győri ETO": "Gyoer"},
    )
    assert index.lookup("GYŐRI ETO").club == "Gyoer"
    assert index.lookup("Unlisted United") is None


def test_csv_parser_and_dated_url():
    ratings = parse_clubelo_csv(
        "Rank,Club,Country,Level,Elo,From,To\n"
        "1,Home,AAA,1,1600.25,2026-07-01,2026-08-01\n"
        "2,Bad,BBB,1,not-a-number,2026-07-01,2026-08-01\n"
    )
    assert ratings == [
        ClubEloRating("Home", "AAA", 1600.25, 1, "2026-07-01", "2026-08-01")
    ]
    assert clubelo_ranking_url("http://example.test/{date}", AS_OF).endswith(
        "/2026-07-13"
    )


def test_fetch_clubelo_is_mocked_and_attributed_to_exact_url():
    class Response:
        text = "Rank,Club,Country,Elo\n1,Home,AAA,1600\n"

        @staticmethod
        def raise_for_status():
            return None

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    ratings, url = fetch_clubelo_ratings(
        as_of=AS_OF,
        url_template="http://ratings.test/{date}",
        session=session,
    )
    assert ratings[0].elo == 1600.0
    assert url == "http://ratings.test/2026-07-13"
    assert session.calls[0][1]["headers"]["Accept"] == "text/csv"


def test_transparent_formula_produces_valid_90m_distribution():
    result = predict_fixture(_fixture(), _ratings(), as_of=AS_OF)
    assert result["status"] == "ok"
    probabilities = result["probabilities_90m"]
    assert sum(probabilities.values()) == pytest.approx(1.0)
    assert probabilities["home_win"] > probabilities["away_win"]
    assert result["expected_goals_90m"]["home"] + result["expected_goals_90m"]["away"] == pytest.approx(2.65)
    assert result["qualification"] is None
    intervals = result["uncertainty_90m"]["intervals"]
    for outcome, probability in probabilities.items():
        assert intervals[outcome]["low"] <= probability <= intervals[outcome]["high"]


def test_aggregate_only_changes_separate_advancement_simulation():
    first_leg = predict_fixture(_fixture(), _ratings(), as_of=AS_OF, simulations=5_000)
    second_leg = predict_fixture(
        _fixture(leg=2, aggregate_home_score=0, aggregate_away_score=2),
        _ratings(),
        as_of=AS_OF,
        simulations=5_000,
    )
    other_aggregate = predict_fixture(
        _fixture(leg=2, aggregate_home_score=2, aggregate_away_score=0),
        _ratings(),
        as_of=AS_OF,
        simulations=5_000,
    )
    assert first_leg["probabilities_90m"] == second_leg["probabilities_90m"]
    assert second_leg["probabilities_90m"] == other_aggregate["probabilities_90m"]
    assert first_leg["qualification"] is None
    assert second_leg["qualification"]["home_to_advance"] < other_aggregate["qualification"]["home_to_advance"]
    assert second_leg["qualification"]["home_to_advance"] + second_leg["qualification"]["away_to_advance"] == pytest.approx(1.0)


def test_unknown_team_and_past_fixture_are_no_prediction_not_imputation():
    missing = predict_fixture(
        _fixture(away="Unknown FC"), _ratings(), as_of=AS_OF
    )
    assert missing["status"] == "no_prediction"
    assert missing["reason"] == "clubelo_team_not_found"
    assert missing["missing_teams"] == ["Unknown FC"]
    assert "probabilities_90m" not in missing

    past = predict_fixture(
        _fixture(kickoff_utc="2026-07-12T18:00:00Z"),
        _ratings(),
        as_of=AS_OF,
    )
    assert past == {
        **{key: past[key] for key in ("fixture_id", "kickoff_utc", "competition", "round", "leg", "home", "away")},
        "status": "no_prediction",
        "reason": "not_a_future_fixture",
    }


def test_coverage_reports_missing_teams():
    summary = coverage_summary(
        [
            {"status": "ok"},
            {"status": "no_prediction", "missing_teams": ["B", "A"]},
            {"status": "no_prediction", "missing_teams": ["A"]},
        ]
    )
    assert summary == {
        "fixtures": 3,
        "predicted": 1,
        "no_prediction": 2,
        "coverage": pytest.approx(1 / 3),
        "missing_teams": ["A", "B"],
    }


def test_offline_cli_writes_json_and_csv_without_network(tmp_path, monkeypatch):
    fixtures_path = tmp_path / "fixtures.json"
    ratings_path = tmp_path / "ratings.csv"
    json_path = tmp_path / "predictions.json"
    csv_path = tmp_path / "predictions.csv"
    fixtures_path.write_text(json.dumps([_fixture()]), encoding="utf-8")
    ratings_path.write_text(
        "Rank,Club,Country,Elo\n1,Home,AAA,1600\n2,Away,BBB,1500\n",
        encoding="utf-8",
    )

    def network_forbidden(*args, **kwargs):
        raise AssertionError("offline CLI attempted a network request")

    monkeypatch.setattr("requests.sessions.Session.request", network_forbidden)
    exit_code = cli_main(
        [
            "--mode", "offline",
            "--as-of", "2026-07-13T00:00:00Z",
            "--fixtures-json", str(fixtures_path),
            "--ratings-csv", str(ratings_path),
            "--output-json", str(json_path),
            "--output-csv", str(csv_path),
            "--simulations", "1000",
        ]
    )
    assert exit_code == 0
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["coverage"]["coverage"] == 1.0
    assert payload["sources"]["ratings"]["provider"] == "ClubElo"
    assert "no demonstrated betting or CLV edge" in payload["limitations"][0]
    assert csv_path.read_text(encoding="utf-8").splitlines()[1]


@pytest.mark.parametrize(
    "calibration",
    [
        EloPoissonCalibration(total_goals=0),
        EloPoissonCalibration(elo_denominator=0),
        EloPoissonCalibration(elo_uncertainty=-1),
    ],
)
def test_invalid_calibration_is_rejected(calibration):
    with pytest.raises(ValueError):
        predict_fixture(_fixture(), _ratings(), as_of=AS_OF, calibration=calibration)
