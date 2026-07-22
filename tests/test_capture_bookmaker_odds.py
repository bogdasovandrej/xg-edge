from __future__ import annotations

from datetime import datetime, timezone

from scripts.capture_bookmaker_odds import (
    quota_request_mode,
    required_sport_keys,
    sport_key_for_fixture,
)


NOW = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)


def _fixture(identity: str, competition: str, kickoff: str) -> dict:
    return {"id": identity, "competition": competition, "kickoff_utc": kickoff}


def test_maps_supported_competitions_without_guessing_unknowns() -> None:
    assert sport_key_for_fixture({"competition": "FIFA World Cup™"}) == "soccer_fifa_world_cup"
    assert sport_key_for_fixture({"competition": "UEFA Champions League"}) == "soccer_uefa_champs_league"
    assert sport_key_for_fixture({"competition": "Premier League"}) == "soccer_epl"
    assert sport_key_for_fixture({"competition": "La Liga"}) == "soccer_spain_la_liga"
    assert sport_key_for_fixture({"competition": "Bundesliga"}) == "soccer_germany_bundesliga"
    assert sport_key_for_fixture({"competition": "Serie A"}) == "soccer_italy_serie_a"
    assert sport_key_for_fixture({"competition": "Ligue 1"}) == "soccer_france_ligue_one"
    assert sport_key_for_fixture({"competition": "Friendly"}) is None


def test_requests_new_fixtures_and_closing_window_but_skips_tracked_future() -> None:
    fixtures = [
        _fixture("new", "UEFA Champions League", "2026-07-20T12:00:00Z"),
        _fixture("tracked", "FIFA World Cup™", "2026-07-20T12:00:00Z"),
        _fixture("close", "FIFA World Cup™", "2026-07-14T12:45:00Z"),
        _fixture("past", "UEFA Champions League", "2026-07-14T11:00:00Z"),
    ]
    ledger = {"fixtures": {"tracked": {}, "close": {}}}
    keys = required_sport_keys(
        fixtures, ledger, now=NOW, closing_window_minutes=60, discovery_days=14
    )
    assert keys == ["soccer_fifa_world_cup", "soccer_uefa_champs_league"]


def test_discovery_poll_has_cooldown_but_closing_window_does_not() -> None:
    fixtures = [
        _fixture("new", "UEFA Champions League", "2026-07-20T12:00:00Z"),
        _fixture("close", "FIFA World Cup™", "2026-07-14T12:45:00Z"),
    ]
    snapshot = {
        "snapshot_at": "2026-07-14T11:50:00Z",
        "requested_sport_keys": [
            "soccer_fifa_world_cup", "soccer_uefa_champs_league",
        ],
    }
    keys = required_sport_keys(
        fixtures, {"fixtures": {}}, now=NOW, closing_window_minutes=60,
        discovery_days=14, last_snapshot=snapshot, discovery_cooldown_hours=24,
    )
    assert keys == ["soccer_fifa_world_cup"]


def test_per_sport_cooldown_does_not_make_another_sport_look_fresh() -> None:
    fixtures = [
        _fixture("wc", "FIFA World Cup™", "2026-07-20T12:00:00Z"),
        _fixture("ucl", "UEFA Champions League", "2026-07-20T12:00:00Z"),
    ]
    snapshot = {
        "snapshot_at": "2026-07-14T11:50:00Z",
        "sport_poll_times": {
            "soccer_fifa_world_cup": {"received_at": "2026-07-14T11:50:00Z"},
        },
    }
    assert required_sport_keys(
        fixtures, {"fixtures": {}}, now=NOW, closing_window_minutes=60,
        discovery_days=14, last_snapshot=snapshot,
    ) == ["soccer_uefa_champs_league"]


def test_quota_reserve_disables_discovery_and_zero_has_weekly_probe() -> None:
    low = {"snapshot_at": "2026-07-14T11:00:00Z", "quota": {"remaining": 25}}
    assert quota_request_mode(low, now=NOW) == "closing_only"
    empty = {"snapshot_at": "2026-07-14T11:00:00Z", "quota": {"remaining": 0}}
    assert quota_request_mode(empty, now=NOW) == "blocked"
    old = {"snapshot_at": "2026-07-01T11:00:00Z", "quota": {"remaining": 0}}
    assert quota_request_mode(old, now=NOW) == "probe"
    fixtures = [
        _fixture("new", "UEFA Champions League", "2026-07-20T12:00:00Z"),
        _fixture("close", "FIFA World Cup™", "2026-07-14T12:45:00Z"),
    ]
    assert required_sport_keys(
        fixtures, {"fixtures": {}}, now=NOW, closing_window_minutes=60,
        discovery_days=14, include_discovery=False,
    ) == ["soccer_fifa_world_cup"]


def test_hourly_quota_is_reenabled_after_provider_reset() -> None:
    reset = {
        "snapshot_at": "2026-07-14T11:00:00Z",
        "quota": {
            "remaining": 0,
            "reset": "2026-07-14T11:59:59Z",
        },
    }
    assert quota_request_mode(reset, now=NOW) == "normal"
