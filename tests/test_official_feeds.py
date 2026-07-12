"""Official feed normalization and CLI tests; real network access is forbidden."""
from __future__ import annotations

import csv
import json

import pytest
import requests

import scripts.fetch_current_fixtures as fixture_cli
from xgedge.data.official_feeds import (
    FIXTURE_FIELDS,
    fetch_fifa_fixtures,
    fetch_uefa_fixtures,
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
        "Stadium": {"Name": [{"Locale": "en-GB", "Description": "MetLife Stadium"}]},
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
        "venue": "MetLife Stadium", "round": "Semi-final", "stage": "Final Stage",
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


def _uefa_match(match_id, date, *, status="UPCOMING", second_leg=False):
    match = {
        "id": match_id,
        "status": status,
        "seasonYear": "2027",
        "kickOffTime": {"dateTime": date},
        "competition": {"id": "1", "metaData": {"name": "UEFA Champions League"}},
        "competitionPhase": "QUALIFYING",
        "homeTeam": {"id": "kups", "internationalName": "KuPS Kuopio"},
        "awayTeam": {"id": "vardar", "internationalName": "Vardar"},
        "stadium": {"translations": {"officialName": {"EN": "Kuopio Stadium"}}},
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
    assert session.calls[0][1]["params"]["offset"] == 0
    assert session.calls[1][1]["params"]["offset"] == 2
    assert session.calls[0][1]["params"]["competitionId"] == "custom-ucl"
    assert session.calls[0][1]["params"]["seasonYear"] == "2099"


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
    fifa.update({"source": "fifa", "id": "2", "kickoff_utc": "2026-07-15T19:00:00Z"})
    uefa = {field: None for field in FIXTURE_FIELDS}
    uefa.update({"source": "uefa", "id": "1", "kickoff_utc": "2026-07-14T15:00:00Z"})
    monkeypatch.setattr(fixture_cli, "fetch_fifa_fixtures", lambda **kwargs: [fifa])
    monkeypatch.setattr(fixture_cli, "fetch_uefa_fixtures", lambda **kwargs: [uefa])

    fixture_cli.main([
        "--output-dir", str(tmp_path),
        "--as-of", "2026-07-13T00:00:00Z",
        "--to-date", "2026-07-20T00:00:00Z",
        "--fifa-url", "https://custom/fifa",
        "--uefa-url", "https://custom/uefa",
    ])

    payload = json.loads((tmp_path / "current_fixtures.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in payload] == ["1", "2"]
    with (tmp_path / "current_fixtures.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0]) == FIXTURE_FIELDS
    assert [row["id"] for row in rows] == ["1", "2"]
    assert "wrote 2 fixtures" in capsys.readouterr().out
