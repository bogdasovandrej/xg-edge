"""Leakage guards and provider adapters; all HTTP calls are mocked."""
from __future__ import annotations

import pytest
import requests

from xgedge.data.availability_providers import (
    AvailabilityProvider,
    OptaProviderContract,
    SportmonksInjuryProvider,
)
from xgedge.data.point_in_time import (
    aggregate_availability_features,
    assert_prematch_snapshot,
    available_snapshot,
    unavailable_snapshot,
)
from xgedge.data.uefa_match_context import fetch_uefa_match_context


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
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
def no_real_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network is forbidden in point-in-time tests")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


def test_uefa_context_uses_official_resources_and_normalizes_events():
    lineups = {
        "announcedAt": "2026-07-14T17:55:00Z",
        "homeTeam": {
            "id": "fra",
            "internationalName": "France",
            "startingXI": [{
                "player": {"id": "p1", "internationalName": "Player One"},
                "expectedMinutes": 82,
            }],
            "substitutes": [{
                "player": {"id": "p2", "internationalName": "Player Two"},
                "expectedMinutes": 0,
                "minutesPlayed": 0,
            }],
        },
        "awayTeam": {
            "id": "bra",
            "internationalName": "Brazil",
            "startingXI": [{
                "player": {"id": "p3", "internationalName": "Player Three"},
            }],
        },
        "referees": [{
            "role": "REFEREE",
            "person": {"id": "ref-1", "internationalName": "Jane Referee"},
        }],
    }
    events = {
        "updatedAt": "2026-07-14T19:40:00Z",
        "events": [
            {"id": "g1", "type": "GOAL", "minute": 12, "side": "home"},
            {
                "id": "r1",
                "type": "CARD",
                "cardType": "RED_CARD",
                "minute": 34,
                "side": "away",
                "teamId": "bra",
                "player": {"id": "p4", "internationalName": "Sent Off"},
            },
            {
                "id": "r2",
                "type": "CARD",
                "cardType": "SECOND_YELLOW_CARD",
                "minute": 45,
                "addedTime": 2,
                "side": "home",
                "teamId": "fra",
                "scoreBefore": {"home": 1, "away": 0},
            },
        ],
        "referees": [],
    }
    session = FakeSession([lineups, events])

    result = fetch_uefa_match_context(
        "2036164",
        snapshot_at="2026-07-14T19:45:00Z",
        base_url="https://match.uefa.test/v5/matches/{match_id}",
        session=session,
    )

    assert [call[0] for call in session.calls] == [
        "https://match.uefa.test/v5/matches/2036164/lineups",
        "https://match.uefa.test/v5/matches/2036164/events",
    ]
    starters = result["lineups"]["records"]
    assert starters[0]["snapshot_at"] == "2026-07-14T19:45:00Z"
    assert starters[0]["announced_at"] == "2026-07-14T17:55:00Z"
    assert starters[0]["player_name"] == "Player One"
    assert starters[0]["expected_minutes"] == 82.0
    assert starters[0]["confirmed_minutes"] is None
    assert starters[1]["lineup_status"] == "substitute"
    assert starters[1]["expected_minutes"] == 0.0
    assert starters[1]["confirmed_minutes"] == 0.0
    cards = result["red_cards"]["records"]
    assert cards[0]["red_card_side"] == "away"
    assert (cards[0]["score_before_home"], cards[0]["score_before_away"]) == (1, 0)
    assert cards[1]["event_type"] == "second_yellow_red"
    assert (cards[1]["minute"], cards[1]["added_time"]) == (45, 2)
    assert result["referees"]["records"][0]["referee_name"] == "Jane Referee"


def test_post_kickoff_snapshot_is_rejected_for_prematch_use():
    with pytest.raises(ValueError, match="post-kickoff"):
        assert_prematch_snapshot(
            "2026-07-14T19:00:01Z", "2026-07-14T19:00:00Z"
        )
    with pytest.raises(ValueError, match="cutoff cannot be after kickoff"):
        aggregate_availability_features(
            team_id="fra",
            kickoff_utc="2026-07-14T19:00:00Z",
            cutoff="2026-07-14T19:00:01Z",
        )


def test_features_select_latest_snapshot_at_cutoff_and_preserve_unknown_injuries():
    old = available_snapshot("uefa_lineups", [{
        "team_id": "fra", "player_id": "old", "lineup_status": "starter",
        "is_confirmed": True, "expected_minutes": 90,
    }], snapshot_at="2026-07-14T17:00:00Z")
    current = available_snapshot("uefa_lineups", [{
        "team_id": "fra", "player_id": "new", "lineup_status": "starter",
        "is_confirmed": True, "expected_minutes": 75,
    }, {
        "team_id": "fra", "player_id": "bench", "lineup_status": "substitute",
        "is_confirmed": True, "expected_minutes": 15,
    }], snapshot_at="2026-07-14T18:00:00Z")
    future = available_snapshot("uefa_lineups", [{
        "team_id": "fra", "player_id": "late", "lineup_status": "starter",
        "is_confirmed": True, "expected_minutes": 90,
    }], snapshot_at="2026-07-14T18:40:00Z")
    missing = unavailable_snapshot(
        "sportmonks", "missing_api_token", snapshot_at="2026-07-14T18:00:00Z"
    )

    features = aggregate_availability_features(
        team_id="fra",
        kickoff_utc="2026-07-14T19:00:00Z",
        cutoff="2026-07-14T18:30:00Z",
        lineup_snapshots=[old, current, future],
        injury_snapshots=[missing],
    )

    assert features["lineup_players"] == 2
    assert features["confirmed_starters"] == 1
    assert features["lineup_expected_minutes"] == 90
    assert features["injury_source_available"] is False
    assert features["unavailable_players"] is None
    assert features["unavailable_expected_minutes"] is None


def test_available_injury_snapshot_can_truthfully_report_zero():
    features = aggregate_availability_features(
        team_id="fra",
        kickoff_utc="2026-07-14T19:00:00Z",
        cutoff="2026-07-14T18:00:00Z",
        injury_snapshots=[available_snapshot(
            "sportmonks", [], snapshot_at="2026-07-14T17:59:00Z"
        )],
    )
    assert features["injury_source_available"] is True
    assert features["unavailable_players"] == 0


def test_sportmonks_missing_token_is_unavailable_without_network(monkeypatch):
    monkeypatch.delenv("SPORTMONKS_API_TOKEN", raising=False)
    snapshot = SportmonksInjuryProvider().fetch_snapshot(
        snapshot_at="2026-07-14T18:00:00Z"
    )
    assert snapshot == {
        "provider": "sportmonks",
        "status": "unavailable",
        "reason": "missing_api_token",
        "snapshot_at": "2026-07-14T18:00:00Z",
        "records": None,
    }


def test_sportmonks_token_adapter_and_opta_contract():
    session = FakeSession([{"data": [{
        "participant_id": 10,
        "player_id": 99,
        "player": {"id": 99, "display_name": "Unavailable Player"},
        "status": "doubtful",
        "updated_at": "2026-07-14T17:30:00Z",
        "expected_minutes": 61,
    }]}])
    provider = SportmonksInjuryProvider(
        "secret-test-token", base_url="https://sportmonks.test/injuries", session=session
    )
    assert isinstance(provider, AvailabilityProvider)
    snapshot = provider.fetch_snapshot(
        team_ids=["10"], snapshot_at="2026-07-14T18:00:00Z"
    )
    assert snapshot["status"] == "available"
    assert snapshot["records"][0]["availability_status"] == "doubtful"
    assert snapshot["records"][0]["expected_minutes"] == 61.0
    _, request = session.calls[0]
    assert request["headers"]["Authorization"] == "Bearer secret-test-token"
    assert request["params"]["filter[participantIds]"] == "10"

    opta = OptaProviderContract().fetch_snapshot(
        snapshot_at="2026-07-14T18:00:00Z"
    )
    assert opta["status"] == "unavailable"
    assert opta["records"] is None
    assert opta["reason"] == "licensed_provider_not_configured"
