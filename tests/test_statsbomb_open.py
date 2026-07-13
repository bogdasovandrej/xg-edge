"""StatsBomb Open Data normalization tests; real network access is forbidden."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import requests

import scripts.fetch_statsbomb_open as statsbomb_cli
from xgedge.data.statsbomb_open import (
    STATSBOMB_ATTRIBUTION,
    build_match_record,
    fetch_catalog,
    fetch_match_record,
    normalize_competition,
    normalize_events,
    normalize_lineups,
    normalize_match,
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
        self.trust_env = True

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(next(self.payloads))


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access is forbidden in StatsBomb tests")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


def _catalog_row():
    return {
        "competition_id": 43,
        "season_id": 106,
        "country_name": "International",
        "competition_name": "FIFA World Cup",
        "competition_gender": "male",
        "competition_youth": False,
        "competition_international": True,
        "season_name": "2022",
        "match_updated": "2024-01-01T00:00:00",
        "match_available": "2024-01-01T00:00:00",
        "match_updated_360": None,
        "match_available_360": None,
    }


def _match_row():
    return {
        "match_id": 3857256,
        "match_date": "2022-12-18",
        "kick_off": "17:00:00.000",
        "competition": {
            "competition_id": 43,
            "country_name": "International",
            "competition_name": "FIFA World Cup",
        },
        "season": {"season_id": 106, "season_name": "2022"},
        "home_team": {
            "home_team_id": 779,
            "home_team_name": "Argentina",
            "home_team_gender": "male",
            "home_team_group": "C",
            "country": {"id": 11, "name": "Argentina"},
        },
        "away_team": {
            "away_team_id": 771,
            "away_team_name": "France",
            "away_team_gender": "male",
            "away_team_group": "D",
            "country": {"id": 78, "name": "France"},
        },
        "home_score": 3,
        "away_score": 3,
        "match_week": 7,
        "competition_stage": {"id": 26, "name": "Final"},
        "stadium": {
            "id": 1000253,
            "name": "Lusail Stadium",
            "country": {"id": 185, "name": "Qatar"},
        },
        "referee": {
            "id": 367,
            "name": "Szymon Marciniak",
            "country": {"id": 182, "name": "Poland"},
        },
        "metadata": {
            "data_version": "1.1.0",
            "shot_fidelity_version": "2",
            "xy_fidelity_version": "2",
        },
        "last_updated": "2023-01-01T00:00:00",
        "last_updated_360": None,
    }


def _events():
    # Deliberately not in chronological order: normalization must sort events.
    return [
        {
            "id": "penalty-goal",
            "period": 1,
            "minute": 30,
            "second": 0,
            "timestamp": "00:30:00.000",
            "type": {"id": 16, "name": "Shot"},
            "team": {"id": 771, "name": "France"},
            "shot": {
                "statsbomb_xg": 0.78,
                "type": {"id": 88, "name": "Penalty"},
                "outcome": {"id": 97, "name": "Goal"},
            },
        },
        {
            "id": "open-play-goal",
            "period": 1,
            "minute": 10,
            "second": 5,
            "timestamp": "00:10:05.000",
            "type": {"id": 16, "name": "Shot"},
            "team": {"id": 779, "name": "Argentina"},
            "shot": {
                "statsbomb_xg": 0.2,
                "type": {"id": 87, "name": "Open Play"},
                "outcome": {"id": 97, "name": "Goal"},
            },
        },
        {
            "id": "straight-red",
            "period": 1,
            "minute": 20,
            "second": 0,
            "timestamp": "00:20:00.000",
            "type": {"id": 22, "name": "Foul Committed"},
            "team": {"id": 771, "name": "France"},
            "foul_committed": {"card": {"id": 5, "name": "Red Card"}},
        },
        {
            "id": "second-yellow",
            "period": 1,
            "minute": 40,
            "second": 0,
            "timestamp": "00:40:00.000",
            "type": {"id": 24, "name": "Bad Behaviour"},
            "team": {"id": 779, "name": "Argentina"},
            "bad_behaviour": {"card": {"id": 7, "name": "Second Yellow"}},
        },
        {
            "id": "saved-shot",
            "period": 2,
            "minute": 65,
            "second": 1,
            "timestamp": "00:20:01.000",
            "type": {"id": 16, "name": "Shot"},
            "team": {"id": 771, "name": "France"},
            "shot": {
                "statsbomb_xg": 0.1,
                "type": {"id": 87, "name": "Open Play"},
                "outcome": {"id": 100, "name": "Saved"},
            },
        },
        {
            "id": "shootout-attempt",
            "period": 5,
            "minute": 121,
            "second": 0,
            "timestamp": "00:01:00.000",
            "type": {"id": 16, "name": "Shot"},
            "team": {"id": 779, "name": "Argentina"},
            "shot": {
                "statsbomb_xg": 0.78,
                "type": {"id": 88, "name": "Penalty"},
                "outcome": {"id": 97, "name": "Goal"},
            },
        },
    ]


def _lineups():
    return [
        {
            "team_id": 779,
            "team_name": "Argentina",
            "lineup": [
                {
                    "player_id": 5503,
                    "player_name": "Lionel Messi",
                    "player_nickname": None,
                    "jersey_number": 10,
                    "country": {"id": 11, "name": "Argentina"},
                    "cards": [
                        {
                            "time": "89:00",
                            "card_type": "Yellow Card",
                            "reason": "Foul Committed",
                            "period": 2,
                        }
                    ],
                    "positions": [
                        {
                            "position_id": 23,
                            "position": "Center Forward",
                            "from": "00:00",
                            "to": None,
                            "from_period": 1,
                            "to_period": 4,
                            "start_reason": "Starting XI",
                            "end_reason": "Final Whistle",
                        }
                    ],
                }
            ],
        },
        {"team_id": 771, "team_name": "France", "lineup": []},
    ]


def test_catalog_and_match_metadata_are_normalized_without_current_claim() -> None:
    competition = normalize_competition(_catalog_row())
    match = normalize_match(_match_row())

    assert competition["competition_id"] == 43
    assert competition["season"] == "2022"
    assert competition["usage_mode"] == "historical_calibration_only"
    assert competition["current_coverage_guaranteed"] is False
    assert match["competition"] == {
        "id": 43,
        "name": "FIFA World Cup",
        "country": "International",
    }
    assert match["home_team"]["id"] == 779
    assert match["away_team"]["name"] == "France"
    assert match["kickoff_local"] == "17:00:00.000"
    assert match["referee"] == {
        "id": 367,
        "name": "Szymon Marciniak",
        "country": {"id": 182, "name": "Poland"},
    }


def test_event_xg_npxg_penalties_and_score_before_red_cards() -> None:
    summary = normalize_events(_events(), home_team_id=779, away_team_id=771)

    assert summary["home"]["xg"] == pytest.approx(0.2)
    assert summary["home"]["npxg"] == pytest.approx(0.2)
    assert summary["home"]["penalties"] == {"taken": 0, "scored": 0, "xg": 0.0}
    assert summary["away"]["xg"] == pytest.approx(0.88)
    assert summary["away"]["npxg"] == pytest.approx(0.1)
    assert summary["away"]["penalties"] == {
        "taken": 1,
        "scored": 1,
        "xg": 0.78,
    }
    assert summary["goals_from_events"] == {"home": 1, "away": 1}
    assert summary["shootout_events_excluded"] == 1
    assert summary["red_cards"][0]["minute"] == 20
    assert summary["red_cards"][0]["team"]["name"] == "France"
    assert summary["red_cards"][0]["score_before"] == {"home": 1, "away": 0}
    assert summary["red_cards"][1]["card"] == "Second Yellow"
    assert summary["red_cards"][1]["score_before"] == {"home": 1, "away": 1}


def test_paired_own_goal_events_are_counted_once() -> None:
    paired = [
        {
            "period": 1,
            "minute": 39,
            "second": 50,
            "timestamp": "00:39:50.102",
            "type": {"id": 25, "name": "Own Goal For"},
            "team": {"id": 779, "name": "Argentina"},
        },
        {
            "period": 1,
            "minute": 39,
            "second": 50,
            "timestamp": "00:39:50.102",
            "type": {"id": 20, "name": "Own Goal Against"},
            "team": {"id": 771, "name": "France"},
        },
    ]
    summary = normalize_events(paired, home_team_id=779, away_team_id=771)
    assert summary["goals_from_events"] == {"home": 1, "away": 0}


def test_lineups_and_compact_match_record_keep_attribution() -> None:
    lineups = normalize_lineups(_lineups())
    record = build_match_record(
        _match_row(),
        _events(),
        _lineups(),
        source_urls=["https://example.test/events/3857256.json"],
        fetched_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )

    assert lineups["team_count"] == 2
    assert lineups["player_count"] == 1
    assert lineups["teams"][0]["players"][0]["positions"][0]["position"] == "Center Forward"
    assert record["usage_mode"] == "historical_calibration_only"
    assert record["current_coverage_guaranteed"] is False
    assert record["provenance"]["attribution"] == STATSBOMB_ATTRIBUTION
    assert record["provenance"]["fetched_at"] == "2026-07-14T00:00:00Z"
    assert "raw event" not in json.dumps(record).casefold()


def test_fetchers_use_only_expected_bounded_resources() -> None:
    catalog_session = FakeSession([[_catalog_row()]])
    catalog = fetch_catalog(base_url="https://example.test/data", session=catalog_session)
    assert len(catalog) == 1
    assert catalog_session.calls[0][0] == "https://example.test/data/competitions.json"

    match_session = FakeSession([[_match_row()], _events(), _lineups()])
    record = fetch_match_record(
        43,
        106,
        3857256,
        base_url="https://example.test/data",
        session=match_session,
        fetched_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )
    assert [call[0] for call in match_session.calls] == [
        "https://example.test/data/matches/43/106.json",
        "https://example.test/data/events/3857256.json",
        "https://example.test/data/lineups/3857256.json",
    ]
    assert record["events"]["red_card_count"] == 2


def test_cli_defaults_to_small_catalog_only(tmp_path, monkeypatch, capsys) -> None:
    session = FakeSession([[_catalog_row()]])
    monkeypatch.setattr(statsbomb_cli.requests, "Session", lambda: session)
    output = tmp_path / "catalog.json"

    statsbomb_cli.main([
        "--output",
        str(output),
        "--base-url",
        "https://example.test/data",
    ])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["snapshot_type"] == "historical_catalog"
    assert payload["current_coverage_guaranteed"] is False
    assert len(payload["competition_seasons"]) == 1
    assert [call[0] for call in session.calls] == [
        "https://example.test/data/competitions.json"
    ]
    assert session.trust_env is False
    assert "historical_catalog" in capsys.readouterr().out


def test_cli_rejects_unbounded_match_request(tmp_path) -> None:
    with pytest.raises(SystemExit):
        statsbomb_cli.main([
            "--output",
            str(tmp_path / "bad.json"),
            "--match-id",
            "3857256",
        ])
