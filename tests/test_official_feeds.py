"""Official feed normalization and CLI tests; real network access is forbidden."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import requests

import scripts.fetch_current_fixtures as fixture_cli
import scripts.fetch_uefa_history as history_cli
from xgedge.data.official_feeds import (
    FIXTURE_FIELDS,
    UEFA_CLUB_COMPETITIONS,
    UEFA_CONFERENCE_LEAGUE_COMPETITION_ID,
    UEFA_EUROPA_LEAGUE_COMPETITION_ID,
    fetch_uefa_club_fixtures,
    fetch_uefa_completed_matches,
    fetch_fifa_fixtures,
    fetch_uefa_fixtures,
    normalize_uefa_completed_match,
    resolve_uefa_competitions,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(next(self.payloads))


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access is forbidden in official-feed tests")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


def _fifa_match(match_id="wc-semi", date="2026-07-14T19:00:00Z", score=None):
    return {
        "IdMatch": match_id,
        "IdCompetition": "17",
        "IdSeason": "285023",
        "Date": date,
        "Home": {"IdTeam": "fra", "ShortClubName": "France"},
        "Away": {"IdTeam": "bra", "TeamName": [{"Locale": "en-GB", "Description": "Brazil"}]},
        "HomeTeamScore": score,
        "AwayTeamScore": score,
        "CompetitionName": [{"Locale": "en-GB", "Description": "FIFA World Cup"}],
        "StageName": [{"Locale": "en-GB", "Description": "Final Stage"}],
        "GroupName": [{"Locale": "en-GB", "Description": "Semi-final"}],
        "Stadium": {
            "Name": [{"Locale": "en-GB", "Description": "MetLife Stadium"}],
            "CityName": [{"Locale": "en-GB", "Description": "New York"}],
            "Latitude": 40.8135, "Longitude": -74.0745,
        },
        "Officials": [
            {"OfficialType": 4, "Name": [{"Description": "Fourth Official"}]},
            {"OfficialType": 1, "Name": [{"Locale": "en-GB", "Description": "Jane Referee"}]},
        ],
    }


def test_fifa_feed_filters_past_and_normalizes_configurable_request() -> None:
    session = FakeSession([{"Results": [
        _fifa_match(),
        _fifa_match("played", "2026-07-12T19:00:00Z", score=2),
    ]}])
    result = fetch_fifa_fixtures(
        base_url="https://example.test/fifa",
        competition_id="custom-comp",
        season_id="custom-season",
        as_of="2026-07-13T00:00:00Z",
        to_date="2026-07-20T00:00:00Z",
        count=25,
        session=session,
    )

    assert len(result) == 1
    assert result[0] == {
        "source": "fifa", "id": "wc-semi", "competition_id": "17",
        "competition": "FIFA World Cup", "season_id": "285023",
        "kickoff_utc": "2026-07-14T19:00:00Z", "home_id": "fra",
        "home": "France", "away_id": "bra", "away": "Brazil",
        "venue": "MetLife Stadium", "venue_city": "New York",
        "latitude": 40.8135, "longitude": -74.0745,
        "round": "Semi-final", "stage": "Final Stage",
        "leg": None, "first_leg_home_score": None, "first_leg_away_score": None,
        "aggregate_home_score": None, "aggregate_away_score": None,
        "referee": "Jane Referee",
    }
    url, kwargs = session.calls[0]
    assert url == "https://example.test/fifa"
    assert kwargs["params"]["idCompetition"] == "custom-comp"
    assert kwargs["params"]["idSeason"] == "custom-season"
    assert kwargs["params"]["count"] == 25


def test_fifa_feed_keeps_tbd_knockout_placeholders() -> None:
    final = _fifa_match("wc-final", "2026-07-19T19:00:00Z")
    final.update({
        "Home": None,
        "Away": None,
        "PlaceHolderA": "W101",
        "PlaceHolderB": "W102",
    })
    result = fetch_fifa_fixtures(
        as_of="2026-07-13T00:00:00Z",
        to_date="2026-07-20T00:00:00Z",
        session=FakeSession([{"Results": [final]}]),
    )

    assert result[0]["home"] == "W101"
    assert result[0]["home_id"] == "placeholder:W101"
    assert result[0]["away"] == "W102"


def _uefa_match(
    match_id,
    date,
    *,
    status="UPCOMING",
    second_leg=False,
    competition_id="1",
    competition_code="UCL",
    competition_name="UEFA Champions League",
):
    match = {
        "id": match_id,
        "status": status,
        "seasonYear": "2027",
        "kickOffTime": {"dateTime": date},
        "competition": {
            "id": competition_id,
            "code": competition_code,
            "metaData": {"name": competition_name},
        },
        "competitionPhase": "QUALIFYING",
        "homeTeam": {"id": "kups", "internationalName": "KuPS Kuopio"},
        "awayTeam": {"id": "vardar", "internationalName": "Vardar"},
        "stadium": {
            "translations": {"officialName": {"EN": "Kuopio Stadium"}},
            "city": {"translations": {"name": {"EN": "Kuopio"}}},
            "geolocation": {"latitude": 62.895198, "longitude": 27.666192},
        },
        "round": {"metaData": {"name": "First qualifying round"}},
        "referees": [
            {"role": "REFEREE_OBSERVER", "person": {"translations": {"name": {"EN": "Observer"}}}},
            {"role": "REFEREE", "person": {"translations": {"name": {"EN": "Matthew Ref"}}}},
        ],
    }
    if second_leg:
        match.update({
            "type": "SECOND_LEG", "leg": {"number": 2},
            "relatedMatches": [{
                "id": "first", "type": "FIRST_LEG",
                "homeTeam": {"id": "vardar", "internationalName": "Vardar"},
                "awayTeam": {"id": "kups", "internationalName": "KuPS Kuopio"},
                "score": {"regular": {"home": 0, "away": 2}},
            }],
        })
    return match


def test_verified_uefa_competition_registry_is_explicit_and_resolvable() -> None:
    assert [
        (row.key, row.competition_id, row.code, row.name)
        for row in UEFA_CLUB_COMPETITIONS
    ] == [
        ("ucl", "1", "UCL", "UEFA Champions League"),
        ("uel", UEFA_EUROPA_LEAGUE_COMPETITION_ID, "UEL", "UEFA Europa League"),
        (
            "uecl",
            UEFA_CONFERENCE_LEAGUE_COMPETITION_ID,
            "UECL",
            "UEFA Conference League",
        ),
    ]
    assert [row.key for row in resolve_uefa_competitions(None)] == ["ucl", "uel", "uecl"]
    assert [row.key for row in resolve_uefa_competitions(["uecl", "ucl", "uecl"])] == [
        "uecl",
        "ucl",
    ]
    with pytest.raises(ValueError, match="cannot be combined"):
        resolve_uefa_competitions(["all", "uel"])


def test_live_workflow_keeps_all_three_uefa_qualifiers_enabled() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "live-predictions.yml"
    ).read_text(encoding="utf-8")

    assert workflow.count("--uefa-competition all") == 2
    assert "--source uefa" in workflow
    assert "--limit 200" in workflow
    assert "Champions, Europa and Conference League qualifiers" in workflow
    assert "Official fixture refresh unavailable" not in workflow


def test_uefa_feed_paginates_and_orients_first_leg_to_current_home() -> None:
    future = _uefa_match("ucl-2", "2026-07-14T15:00:00Z", second_leg=True)
    filler = _uefa_match("ucl-3", "2026-07-15T15:00:00Z")
    finished = _uefa_match("ucl-old", "2026-07-14T10:00:00Z", status="FINISHED")
    session = FakeSession([[future, filler], [finished]])

    result = fetch_uefa_fixtures(
        base_url="https://example.test/uefa",
        competition_id="custom-ucl",
        season_year="2099",
        as_of="2026-07-13T00:00:00Z",
        to_date="2026-07-20T00:00:00Z",
        page_size=2,
        session=session,
    )

    assert [row["id"] for row in result] == ["ucl-2", "ucl-3"]
    leg = result[0]
    assert (leg["first_leg_home_score"], leg["first_leg_away_score"]) == (2, 0)
    assert (leg["aggregate_home_score"], leg["aggregate_away_score"]) == (2, 0)
    assert leg["leg"] == 2
    assert leg["referee"] == "Matthew Ref"
    assert leg["venue"] == "Kuopio Stadium"
    assert leg["venue_city"] == "Kuopio"
    assert leg["latitude"] == pytest.approx(62.895198)
    assert session.calls[0][1]["params"]["offset"] == 0
    assert session.calls[1][1]["params"]["offset"] == 2
    assert session.calls[0][1]["params"]["competitionId"] == "custom-ucl"
    assert session.calls[0][1]["params"]["seasonYear"] == "2099"


def test_multi_uefa_feed_checks_official_metadata_and_keeps_dynamic_fields() -> None:
    payloads = []
    for competition in UEFA_CLUB_COMPETITIONS:
        payloads.append([
            _uefa_match(
                f"{competition.key}-1",
                "2026-07-22T18:00:00Z",
                competition_id=competition.competition_id,
                competition_code=competition.code,
                competition_name=competition.name,
            )
        ])
    session = FakeSession(payloads)

    result = fetch_uefa_club_fixtures(
        competitions=UEFA_CLUB_COMPETITIONS,
        as_of="2026-07-21T00:00:00Z",
        to_date="2026-07-23T00:00:00Z",
        session=session,
    )

    assert [(row["id"], row["competition"]) for row in result] == [
        ("ucl-1", "UEFA Champions League"),
        ("uecl-1", "UEFA Conference League"),
        ("uel-1", "UEFA Europa League"),
    ]
    assert [call[1]["params"]["competitionId"] for call in session.calls] == [
        "1",
        "14",
        "2019",
    ]

    bad = _uefa_match(
        "wrong",
        "2026-07-22T18:00:00Z",
        competition_id="14",
        competition_code="UEL",
        competition_name="UEFA Europa League",
    )
    with pytest.raises(ValueError, match="metadata mismatch"):
        fetch_uefa_club_fixtures(
            competitions=(UEFA_CLUB_COMPETITIONS[0],),
            as_of="2026-07-21T00:00:00Z",
            to_date="2026-07-23T00:00:00Z",
            session=FakeSession([[bad]]),
        )


def test_completed_uefa_history_uses_regular_score_and_never_invents_xg() -> None:
    finished = _uefa_match(
        "uecl-finished",
        "2026-07-16T18:00:00Z",
        status="FINISHED",
        competition_id="2019",
        competition_code="UECL",
        competition_name="UEFA Conference League",
    )
    finished["score"] = {
        "regular": {"home": 1, "away": 1},
        "total": {"home": 2, "away": 1},
    }
    normalized = normalize_uefa_completed_match(finished)
    assert normalized is not None
    assert (normalized["home_goals_90"], normalized["away_goals_90"]) == (1, 1)
    assert normalized["score_basis"] == "uefa_score_regular_90m"
    assert normalized["status"] == "FINISHED"
    assert normalized["official"] is True
    assert normalized["scope"] == "club"
    assert normalized["competition_level"] == "uefa_conference_league"
    assert normalized["provenance"]["source"] == "official_uefa_match_api"
    assert normalized["provenance"]["xg"] == "not_provided"
    assert not any("xg_" in key or key.startswith("xg") for key in normalized)

    no_regular = dict(finished)
    no_regular["score"] = {"total": {"home": 2, "away": 1}}
    assert normalize_uefa_completed_match(no_regular) is None


def test_completed_uefa_history_filters_exact_team_and_cutoff() -> None:
    included = _uefa_match("included", "2026-07-16T18:00:00Z", status="FINISHED")
    included["score"] = {"regular": {"home": 2, "away": 0}}
    excluded_team = _uefa_match("other", "2026-07-15T18:00:00Z", status="FINISHED")
    excluded_team["homeTeam"] = {"id": "other-home", "internationalName": "Other"}
    excluded_team["awayTeam"] = {"id": "other-away", "internationalName": "Else"}
    excluded_team["score"] = {"regular": {"home": 0, "away": 0}}
    upcoming = _uefa_match("upcoming", "2026-07-20T18:00:00Z")
    session = FakeSession([[upcoming, included, excluded_team]])

    result = fetch_uefa_completed_matches(
        as_of="2026-07-19T00:00:00Z",
        from_date="2026-07-01T00:00:00Z",
        team_ids=["kups"],
        page_size=10,
        session=session,
        expected_competition=UEFA_CLUB_COMPETITIONS[0],
    )

    assert [row["id"] for row in result] == ["included"]
    params = session.calls[0][1]["params"]
    assert params["order"] == "DESC"
    assert params["fromDate"] == "2026-07-01"
    assert params["toDate"] == "2026-07-19"


def test_feed_rejects_bad_payload_and_date_range() -> None:
    with pytest.raises(ValueError, match="Results array"):
        fetch_fifa_fixtures(
            as_of="2026-07-13T00:00:00Z", session=FakeSession([{"bad": []}])
        )
    with pytest.raises(ValueError, match="later than"):
        fetch_uefa_fixtures(
            as_of="2026-07-13T00:00:00Z",
            to_date="2026-07-12T00:00:00Z",
            session=FakeSession([]),
        )


def test_cli_writes_deterministic_combined_json_and_csv(tmp_path, monkeypatch, capsys) -> None:
    fifa = {field: None for field in FIXTURE_FIELDS}
    fifa.update({"source": "fifa", "id": "4", "kickoff_utc": "2026-07-15T19:00:00Z"})
    uefa_rows = []
    for index, competition in enumerate(UEFA_CLUB_COMPETITIONS, 1):
        row = {field: None for field in FIXTURE_FIELDS}
        row.update({
            "source": "uefa",
            "id": str(index),
            "competition_id": competition.competition_id,
            "competition": competition.name,
            "kickoff_utc": f"2026-07-14T{14 + index}:00:00Z",
        })
        uefa_rows.append(row)
    monkeypatch.setattr(fixture_cli, "fetch_fifa_fixtures", lambda **kwargs: [fifa])
    seen = {}

    def fake_uefa(**kwargs):
        seen["competitions"] = kwargs["competitions"]
        return uefa_rows

    monkeypatch.setattr(fixture_cli, "fetch_uefa_club_fixtures", fake_uefa)

    fixture_cli.main([
        "--output-dir", str(tmp_path),
        "--as-of", "2026-07-13T00:00:00Z",
        "--to-date", "2026-07-20T00:00:00Z",
        "--fifa-url", "https://custom/fifa",
        "--uefa-url", "https://custom/uefa",
    ])

    payload = json.loads((tmp_path / "current_fixtures.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in payload] == ["1", "2", "3", "4"]
    assert [competition.key for competition in seen["competitions"]] == [
        "ucl",
        "uel",
        "uecl",
    ]
    with (tmp_path / "current_fixtures.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0]) == FIXTURE_FIELDS
    assert [row["id"] for row in rows] == ["1", "2", "3", "4"]
    assert "wrote 4 fixtures" in capsys.readouterr().out


def test_history_cli_uses_exact_fixture_team_ids_and_writes_contract(
    tmp_path, monkeypatch, capsys
) -> None:
    fixtures_path = tmp_path / "fixtures.json"
    output_path = tmp_path / "history.json"
    fixtures_path.write_text(
        json.dumps(
            [
                {
                    "source": "fifa",
                    "home_id": "eng",
                    "away_id": "fra",
                },
                {
                    "source": "uefa",
                    "home_id": "200",
                    "away_id": "100",
                },
                {
                    "source": "uefa",
                    "home_id": "100",
                    "away_id": "300",
                },
            ]
        ),
        encoding="utf-8",
    )
    seen = {}

    def fake_history(**kwargs):
        seen.update(kwargs)
        return [{"id": "finished-1", "score_basis": "uefa_score_regular_90m"}]

    monkeypatch.setattr(history_cli, "fetch_uefa_completed_history", fake_history)
    assert history_cli.main(
        [
            "--fixtures",
            str(fixtures_path),
            "--output",
            str(output_path),
            "--as-of",
            "2026-07-21T12:00:00Z",
            "--generated-at",
            "2026-07-21T12:01:00Z",
            "--uefa-competition",
            "uel",
            "--uefa-competition",
            "uecl",
            "--season-year",
            "2027",
            "--season-year",
            "2026",
        ]
    ) == 0

    assert seen["team_ids"] == ("100", "200", "300")
    assert [row.key for row in seen["competitions"]] == ["uel", "uecl"]
    assert seen["season_years"] == ("2027", "2026")
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "uefa-club-history/1.0"
    assert payload["generated_at_utc"] == "2026-07-21T12:01:00Z"
    assert payload["as_of_utc"] == "2026-07-21T12:00:00Z"
    assert payload["scope"] == "club"
    assert payload["contract"] == {
        "match_status": "FINISHED",
        "official": True,
        "score_basis": "uefa_score_regular_90m",
        "xg": "not_provided",
    }
    assert payload["team_ids"] == ["100", "200", "300"]
    assert payload["matches"] == [
        {"id": "finished-1", "score_basis": "uefa_score_regular_90m"}
    ]
    assert "wrote 1 official UEFA matches for 3 teams" in capsys.readouterr().out
