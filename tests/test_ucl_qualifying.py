from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pytest

import scripts.predict_ucl_qualifying as predictor_cli
from scripts.predict_ucl_qualifying import main as cli_main
from xgedge.experiments.ucl_qualifying import (
    ClubEloIndex,
    ClubEloRating,
    EloPoissonCalibration,
    add_uefa_elo_fallbacks,
    build_team_goal_environment,
    clubelo_ranking_url,
    coverage_summary,
    fetch_clubelo_ratings,
    normalize_team_name,
    parse_clubelo_csv,
    predict_fixture,
    predict_fixtures,
)


AS_OF = datetime(2026, 7, 13, tzinfo=timezone.utc)


def _fixture(**overrides):
    fixture = {
        "id": "ucl-1",
        "kickoff_utc": "2026-07-14T18:00:00Z",
        "competition_id": "1",
        "competition": "UEFA Champions League",
        "season_id": "2027",
        "round": "First qualifying round",
        "stage": "QUALIFYING",
        "leg": 1,
        "home": "Home FC",
        "away": "Away FK",
        "home_id": "home-id",
        "away_id": "away-id",
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


def test_verified_default_aliases_cover_current_uefa_names():
    ratings = [
        ClubEloRating("Mjaellby", "SWE", 1515.6),
        ClubEloRating("Lech", "POL", 1520.9),
        ClubEloRating("Dinamo Zagreb", "CRO", 1577.1),
        ClubEloRating("Gornik", "POL", 1452.0),
        ClubEloRating("Beer-Sheva", "ISR", 1466.1),
        ClubEloRating("Slovan Bratislava", "SLK", 1366.5),
    ]
    index = ClubEloIndex(ratings)

    assert index.lookup("Mjällby").club == "Mjaellby"
    assert index.lookup("Lech Poznań").club == "Lech"
    assert index.lookup("GNK Dinamo").club == "Dinamo Zagreb"
    assert index.lookup("Górnik Zabrze").club == "Gornik"
    assert index.lookup("H. Beer-Sheva").club == "Beer-Sheva"
    assert index.lookup("S. Bratislava").club == "Slovan Bratislava"


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


def test_official_pre_match_history_produces_match_specific_goal_totals():
    history = {"matches": [
        {
            "id": "h1",
            "kickoff_utc": "2026-07-01T18:00:00Z",
            "status": "FINISHED",
            "official": True,
            "home_id": "home-id",
            "away_id": "other-1",
            "home_goals_90": 3,
            "away_goals_90": 2,
        },
        {
            "id": "h2",
            "kickoff_utc": "2026-07-02T18:00:00Z",
            "status": "FINISHED",
            "official": True,
            "home_id": "other-2",
            "away_id": "away-id",
            "home_goals_90": 0,
            "away_goals_90": 0,
        },
        {
            "id": "future-leak",
            "kickoff_utc": "2026-07-14T19:00:00Z",
            "status": "FINISHED",
            "official": True,
            "home_id": "home-id",
            "away_id": "away-id",
            "home_goals_90": 9,
            "away_goals_90": 9,
        },
    ]}
    environment = build_team_goal_environment(history, as_of=AS_OF)
    result = predict_fixture(
        _fixture(), _ratings(), as_of=AS_OF, goal_environment=environment
    )
    expected_total = sum(result["expected_goals_90m"].values())

    assert expected_total != pytest.approx(2.65)
    assert expected_total == pytest.approx((3.0416666667 + 2.2083333333) / 2)
    assert result["expected_goals_basis"]["method"] == (
        "official_uefa_recent_totals_bayesian_shrinkage"
    )
    assert len(result["expected_goals_basis"]["team_histories_used"]) == 2
    assert all(
        row["matches"] == 1
        for row in result["expected_goals_basis"]["team_histories_used"]
    )


def test_official_uefa_elo_fills_clubelo_gap_without_future_leakage():
    fixture = _fixture(away="Unlisted United", away_id="unlisted-id")
    history = {"matches": [
        {
            "id": "past",
            "kickoff_utc": "2026-07-01T18:00:00Z",
            "status": "FINISHED",
            "official": True,
            "scope": "club",
            "home_id": "unlisted-id",
            "away_id": "opponent-id",
            "home_goals_90": 2,
            "away_goals_90": 0,
        },
        {
            "id": "future-leak",
            "kickoff_utc": "2026-07-14T19:00:00Z",
            "status": "FINISHED",
            "official": True,
            "scope": "club",
            "home_id": "unlisted-id",
            "away_id": "opponent-id",
            "home_goals_90": 0,
            "away_goals_90": 9,
        },
    ]}
    clubelo = [ClubEloRating("Home", "AAA", 1600.0)]

    combined, summary = add_uefa_elo_fallbacks(
        [fixture], clubelo, history, as_of=AS_OF
    )
    result = predict_fixtures(
        [fixture], combined, as_of=AS_OF, simulations=1_000
    )[0]

    assert summary == {
        "clubelo": 1,
        "uefa_official_results": 1,
        "uefa_cold_start_prior": 0,
    }
    assert result["status"] == "ok"
    assert result["ratings"]["home"]["source"] == "clubelo"
    assert result["ratings"]["away"]["source"] == "uefa_official_results"
    assert result["ratings"]["away"]["matches"] == 1
    assert result["ratings"]["away"]["elo"] > 1500
    assert result["ratings"]["basis"] == (
        "clubelo_with_point_in_time_uefa_fallback"
    )
    assert result["uncertainty_90m"]["elo_points_plus_minus"] == 75


def test_no_history_team_uses_explicit_neutral_prior_with_wide_uncertainty():
    fixture = _fixture(away="New Club", away_id="new-club-id")
    combined, summary = add_uefa_elo_fallbacks(
        [fixture],
        [ClubEloRating("Home", "AAA", 1600.0)],
        {"matches": []},
        as_of=AS_OF,
    )
    result = predict_fixtures(
        [fixture], combined, as_of=AS_OF, simulations=1_000
    )[0]

    assert summary["uefa_cold_start_prior"] == 1
    assert result["ratings"]["away"]["elo"] == 1500
    assert result["ratings"]["away"]["matches"] == 0
    assert result["ratings"]["away"]["source"] == "uefa_cold_start_prior"
    assert result["uncertainty_90m"]["elo_points_plus_minus"] == 150


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
        **{
            key: past[key]
            for key in (
                "fixture_id", "kickoff_utc", "competition_id", "competition",
                "season_id", "round", "stage", "leg", "home", "away",
            )
        },
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
    assert payload["sources"]["ratings"]["provider"] == (
        "ClubElo + xgedge UEFA Elo fallback"
    )
    assert "no demonstrated betting or CLV edge" in payload["limitations"][0]
    assert csv_path.read_text(encoding="utf-8").splitlines()[1]


def test_live_cli_can_predict_verified_uefa_competitions_without_hardcoding(
    tmp_path, monkeypatch
):
    json_path = tmp_path / "predictions.json"
    csv_path = tmp_path / "predictions.csv"
    fixtures = [
        _fixture(
            id="uel-1",
            competition_id="14",
            competition="UEFA Europa League",
            round="Second qualifying round",
            stage="QUALIFYING",
            leg=1,
        ),
        _fixture(
            id="uecl-1",
            kickoff_utc="2026-07-14T19:00:00Z",
            competition_id="2019",
            competition="UEFA Conference League",
            round="Second qualifying round",
            stage="QUALIFYING",
            leg=2,
            aggregate_home_score=1,
            aggregate_away_score=0,
        ),
    ]
    seen = {}

    def fake_fixtures(**kwargs):
        seen["competition_keys"] = [row.key for row in kwargs["competitions"]]
        return fixtures

    monkeypatch.setattr(predictor_cli, "fetch_uefa_club_fixtures", fake_fixtures)
    monkeypatch.setattr(
        predictor_cli,
        "fetch_clubelo_ratings",
        lambda **kwargs: (
            [
                ClubEloRating("Home", "AAA", 1600.0),
                ClubEloRating("Away", "BBB", 1500.0),
            ],
            "https://ratings.test/2026-07-13",
        ),
    )

    exit_code = predictor_cli.main([
        "--mode", "live",
        "--as-of", "2026-07-13T00:00:00Z",
        "--uefa-competition", "all",
        "--output-json", str(json_path),
        "--output-csv", str(csv_path),
        "--simulations", "1000",
    ])

    assert exit_code == 0
    assert seen["competition_keys"] == ["ucl", "uel", "uecl"]
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert [row["competition"] for row in payload["predictions"]] == [
        "UEFA Europa League",
        "UEFA Conference League",
    ]
    assert [row["competition_id"] for row in payload["predictions"]] == ["14", "2019"]
    assert payload["predictions"][1]["round"] == "Second qualifying round"
    assert payload["predictions"][1]["stage"] == "QUALIFYING"
    assert payload["predictions"][1]["leg"] == 2
    assert [row["code"] for row in payload["sources"]["fixtures"]["competitions"]] == [
        "UCL",
        "UEL",
        "UECL",
    ]


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
