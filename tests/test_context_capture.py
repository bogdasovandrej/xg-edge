from __future__ import annotations

from xgedge.data.point_in_time import available_snapshot

from scripts.capture_uefa_context import capture_context
from scripts.fetch_weather_context import merge_weather_context


NOW = "2026-07-29T15:00:00Z"


def _fixture(fixture_id: str, kickoff: str, *, source: str = "uefa") -> dict:
    return {
        "id": fixture_id,
        "source": source,
        "kickoff_utc": kickoff,
        "home": "Home",
        "away": "Away",
    }


def _context(match_id, *, snapshot_at, **kwargs):
    return {
        "lineups": available_snapshot(
            "uefa_lineups",
            [{
                "match_id": str(match_id), "team_id": "h", "player_id": "p1",
                "player_name": "Starter", "lineup_status": "starter",
            }],
            snapshot_at=snapshot_at,
        ),
        "coaches": available_snapshot(
            "uefa_lineups",
            [{
                "match_id": str(match_id), "team_id": "h", "coach_id": "c1",
                "coach_name": "Coach",
            }],
            snapshot_at=snapshot_at,
        ),
        "referees": available_snapshot(
            "uefa_match",
            [{
                "match_id": str(match_id), "referee_id": "r1",
                "referee_name": "Referee", "role": "referee",
            }],
            snapshot_at=snapshot_at,
        ),
        "red_cards": available_snapshot("uefa_events", [], snapshot_at=snapshot_at),
    }


def test_capture_preserves_weather_and_maps_official_prematch_context() -> None:
    calls = []

    def fetcher(match_id, **kwargs):
        calls.append(str(match_id))
        return _context(match_id, snapshot_at=kwargs["snapshot_at"])

    previous = {
        "fixtures": {
            "near": {
                "weather": available_snapshot(
                    "open_meteo", [{"fixture_id": "near"}], snapshot_at=NOW
                )
            },
            "past": {"weather": {"obsolete": True}},
        }
    }
    fixtures = [
        _fixture("near", "2026-07-29T18:00:00Z"),
        _fixture("far", "2026-07-30T18:00:00Z"),
        _fixture("past", "2026-07-29T14:00:00Z"),
        _fixture("fifa", "2026-07-29T18:00:00Z", source="fifa"),
    ]

    output, stats = capture_context(
        fixtures, previous, now=NOW, fetcher=fetcher, session=object()
    )

    assert calls == ["near"]
    assert stats == {"eligible": 2, "requested": 1, "lineups": 1, "errors": 0}
    assert set(output["fixtures"]) == {"near", "far", "fifa"}
    assert output["fixtures"]["near"]["weather"]["provider"] == "open_meteo"
    assert output["fixtures"]["near"]["lineups"]["records"][0]["player_id"] == "p1"
    assert output["fixtures"]["near"]["coaches"]["records"][0]["coach_id"] == "c1"
    assert output["fixtures"]["near"]["referee"]["records"][0]["referee_id"] == "r1"
    assert "red_cards" not in output["fixtures"]["near"]


def test_capture_rejects_at_kickoff_provider_snapshot_and_preserves_prior() -> None:
    prior = available_snapshot(
        "uefa_lineups",
        [{"match_id": "m", "team_id": "h", "player_id": "old"}],
        snapshot_at="2026-07-29T14:30:00Z",
    )

    def late(match_id, **kwargs):
        return _context(match_id, snapshot_at="2026-07-29T18:00:00Z")

    output, stats = capture_context(
        [_fixture("m", "2026-07-29T18:00:00Z")],
        {"fixtures": {"m": {"lineups": prior}}},
        now=NOW,
        fetcher=late,
        session=object(),
    )

    assert stats["errors"] == 1
    assert output["fixtures"]["m"]["lineups"] == prior


def test_weather_merge_keeps_other_context_and_valid_prior_on_failure(monkeypatch) -> None:
    previous = {
        "fixtures": {
            "m": {
                "lineups": {"status": "available"},
                "weather": available_snapshot(
                    "open_meteo", [{"fixture_id": "m", "temperature_c": 20}],
                    snapshot_at="2026-07-29T14:00:00Z",
                ),
            }
        }
    }

    monkeypatch.setattr(
        "scripts.fetch_weather_context.fetch_fixture_weather",
        lambda *args, **kwargs: {
            "provider": "open_meteo", "status": "unavailable",
            "reason": "temporary", "snapshot_at": NOW, "records": None,
        },
    )
    output = merge_weather_context(
        previous,
        [_fixture("m", "2026-07-29T18:00:00Z")],
        NOW,
        session=object(),
        timeout=1,
    )

    assert output["fixtures"]["m"]["lineups"] == {"status": "available"}
    assert output["fixtures"]["m"]["weather"]["records"][0]["temperature_c"] == 20
