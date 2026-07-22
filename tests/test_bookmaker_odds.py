from __future__ import annotations

from copy import deepcopy

import requests

import xgedge.data.bookmaker_odds as bookmaker_odds
from xgedge.data.bookmaker_odds import (
    OddsApiIoProvider,
    TheOddsApiProvider,
    apply_odds_snapshot_to_live_payload,
    merge_odds_snapshots,
    normalize_odds_api_io_event,
    normalize_odds_event,
)


class Response:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}
    def raise_for_status(self): return None
    def json(self): return self.payload


class Session:
    def __init__(self, payloads): self.payloads = iter(payloads); self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.payloads)


class ErrorSession:
    def get(self, url, **kwargs):
        response = requests.Response()
        response.status_code = 401
        response.url = f"{url}?apiKey={kwargs['params']['apiKey']}"
        raise requests.HTTPError("401 for secret-bearing URL", response=response)


class OddsApiIoErrorSession:
    def get(self, url, **kwargs):
        response = requests.Response()
        response.status_code = 400
        response.url = f"{url}?apiKey={kwargs['params']['apiKey']}"
        response.headers["Content-Type"] = "application/json"
        response._content = b'{"error":"Invalid filter for secret"}'
        raise requests.HTTPError("400 for secret-bearing URL", response=response)


def _fixture():
    return {
        "id": "2048641", "kickoff_utc": "2026-07-14T15:00:00Z",
        "home": "KuPS Kuopio", "away": "Vardar",
    }


def _event():
    return {
        "id": "provider-1", "sport_key": "soccer_uefa_champs_league",
        "commence_time": "2026-07-14T15:00:00Z", "home_team": "KuPS", "away_team": "Vardar",
        "bookmakers": [{
            "key": "pinnacle", "title": "Pinnacle", "last_update": "2026-07-14T12:00:00Z",
            "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "KuPS", "price": 1.80}, {"name": "Draw", "price": 3.60},
                    {"name": "Vardar", "price": 4.50},
                ]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "point": 2.5, "price": 1.95},
                    {"name": "Under", "point": 2.5, "price": 1.85},
                ]},
            ],
        }],
    }


def _odds_api_io_event():
    return {
        "id": 123456,
        "home": "KuPS",
        "away": "Vardar",
        "date": "2026-07-14T15:00:00Z",
        "status": "pending",
        "sport": {"name": "Football", "slug": "football"},
        "league": {
            "name": "UEFA Champions League",
            "slug": "uefa-champions-league",
        },
        "bookmakers": {
            "Bet365": [
                {
                    "name": "ML",
                    "updatedAt": "2026-07-14T12:00:00Z",
                    "odds": [{"home": "1.80", "draw": "3.60", "away": "4.50"}],
                },
                {
                    "name": "Totals",
                    "updatedAt": "2026-07-14T12:00:00Z",
                    "odds": [{"hdp": 2.5, "over": "1.95", "under": "1.85"}],
                },
            ]
        },
    }


def test_normalizes_h2h_totals_and_explicit_alias_match() -> None:
    row = normalize_odds_event(
        _event(), fixtures=[_fixture()], snapshot_at="2026-07-14T12:05:00Z"
    )
    assert row["fixture_id"] == "2048641"
    assert row["match_status"] == "matched"
    assert row["bookmakers"][0]["markets"]["h2h"] == {
        "home": 1.8, "draw": 3.6, "away": 4.5,
    }
    assert row["bookmakers"][0]["markets"]["totals"][0]["line"] == 2.5


def test_normalizes_odds_api_io_h2h_totals_and_provenance() -> None:
    row = normalize_odds_api_io_event(
        _odds_api_io_event(),
        fixtures=[_fixture()],
        snapshot_at="2026-07-14T12:05:00Z",
    )
    assert row["fixture_id"] == "2048641"
    assert row["source_provider"] == "odds_api_io"
    assert row["bookmakers"][0]["markets"]["h2h"] == {
        "home": 1.8,
        "draw": 3.6,
        "away": 4.5,
    }
    assert row["bookmakers"][0]["markets"]["totals"][0] == {
        "line": 2.5,
        "over": 1.95,
        "under": 1.85,
    }


def test_odds_api_io_batches_matched_events_and_keeps_quota() -> None:
    event_without_odds = {
        key: value for key, value in _odds_api_io_event().items()
        if key != "bookmakers"
    }
    session = Session([
        Response([event_without_odds], {
            "x-ratelimit-limit": "100",
            "x-ratelimit-remaining": "99",
        }),
        Response([_odds_api_io_event()], {
            "x-ratelimit-limit": "100",
            "x-ratelimit-remaining": "98",
            "x-ratelimit-reset": "2026-07-14T13:00:00Z",
        }),
    ])
    result = OddsApiIoProvider(
        api_key="\n secret \r\n",
        base_url="https://odds.test/v3",
        session=session,
    ).fetch_snapshot(
        sport_keys=["soccer_uefa_champs_league"],
        fixtures=[_fixture()],
        snapshot_at="2026-07-14T12:05:00Z",
    )

    assert result["provider"] == "odds_api_io"
    assert result["status"] == "available"
    assert result["records"][0]["fixture_id"] == "2048641"
    assert result["quota"] == {
        "remaining": 98,
        "limit": 100,
        "reset": "2026-07-14T13:00:00Z",
    }
    assert session.calls[0][0] == "https://odds.test/v3/events"
    assert session.calls[0][1]["params"]["sport"] == "football"
    assert session.calls[0][1]["params"]["apiKey"] == "secret"
    assert session.calls[1][0] == "https://odds.test/v3/odds/multi"
    assert session.calls[1][1]["params"]["eventIds"] == "123456"
    assert "secret" not in str(result)


def test_missing_key_is_explicit_and_makes_no_request() -> None:
    session = Session([])
    result = TheOddsApiProvider(api_key=None, session=session).fetch_snapshot(
        sport_keys=["soccer_uefa_champs_league"], fixtures=[_fixture()],
        snapshot_at="2026-07-14T12:05:00Z",
    )
    assert result["status"] == "unavailable"
    assert result["reason"] == "missing_api_key"
    assert session.calls == []


def test_odds_api_io_missing_key_is_explicit_and_makes_no_request(monkeypatch) -> None:
    monkeypatch.delenv("ODDS_API_IO_KEY", raising=False)
    session = Session([])
    result = OddsApiIoProvider(api_key=None, session=session).fetch_snapshot(
        sport_keys=["soccer_uefa_champs_league"], fixtures=[_fixture()],
        snapshot_at="2026-07-14T12:05:00Z",
    )
    assert result["provider"] == "odds_api_io"
    assert result["status"] == "unavailable"
    assert result["reason"] == "missing_api_key"
    assert session.calls == []


def test_odds_api_io_http_error_has_stage_and_redacted_safe_detail() -> None:
    result = OddsApiIoProvider(
        api_key="secret", session=OddsApiIoErrorSession()
    ).fetch_snapshot(
        sport_keys=["soccer_uefa_champs_league"], fixtures=[_fixture()],
        snapshot_at="2026-07-14T12:05:00Z",
    )
    assert result["status"] == "unavailable"
    assert result["errors"] == [{
        "sport_key": "football",
        "error": (
            "HTTPError: stage=events; status=400; "
            "detail=Invalid filter for ***"
        ),
    }]
    assert "secret" not in str(result)


def test_provider_keeps_quota_headers_and_does_not_expose_key() -> None:
    session = Session([Response([_event()], {
        "x-requests-remaining": "499", "x-requests-used": "1", "x-requests-last": "1",
    })])
    result = TheOddsApiProvider(
        api_key="\n secret \r\n", base_url="https://example.test/v4", session=session
    ).fetch_snapshot(
        sport_keys=["soccer_uefa_champs_league"], fixtures=[_fixture()],
        snapshot_at="2026-07-14T12:05:00Z",
    )
    assert result["status"] == "available"
    assert result["quota"] == {"remaining": 499, "used": 1, "last": 1}
    assert result["snapshot_at"] == "2026-07-14T12:05:00Z"
    assert result["records"][0]["received_at"] == "2026-07-14T12:05:00Z"
    assert result["sport_poll_times"]["soccer_uefa_champs_league"] == {
        "requested_at": "2026-07-14T12:05:00Z",
        "received_at": "2026-07-14T12:05:00Z",
        "status": "available",
    }
    assert "secret" not in str(result)
    assert session.calls[0][1]["params"]["apiKey"] == "secret"
    assert session.calls[0][1]["params"]["markets"] == "h2h"


def test_production_clock_is_captured_per_sport_after_http(monkeypatch) -> None:
    moments = iter([
        "2026-07-14T12:00:00Z", "2026-07-14T12:00:02Z",
        "2026-07-14T12:00:03Z", "2026-07-14T12:00:08Z",
    ])
    monkeypatch.setattr(bookmaker_odds, "_utc_now", lambda: next(moments))
    second = deepcopy(_event())
    second["id"] = "provider-2"
    session = Session([Response([_event()]), Response([second])])
    result = TheOddsApiProvider(api_key="secret", session=session).fetch_snapshot(
        sport_keys=["sport-b", "sport-a"], fixtures=[_fixture()]
    )
    assert result["sport_poll_times"] == {
        "sport-a": {
            "requested_at": "2026-07-14T12:00:00Z",
            "received_at": "2026-07-14T12:00:02Z",
            "status": "available",
        },
        "sport-b": {
            "requested_at": "2026-07-14T12:00:03Z",
            "received_at": "2026-07-14T12:00:08Z",
            "status": "available",
        },
    }
    assert [row["received_at"] for row in result["records"]] == [
        "2026-07-14T12:00:02Z", "2026-07-14T12:00:08Z",
    ]
    assert result["snapshot_at"] == "2026-07-14T12:00:08Z"


def _forecast_payload(
    *, generated_at="2026-07-14T12:00:00Z", kickoff=None,
    forecast_generated_at=None,
):
    payload = {
        "generated_at": generated_at,
        "forecasts": [{
            "id": "2048641",
            "kickoff_utc": kickoff or "2026-07-14T15:00:00Z",
            "p_home": .50, "p_draw": .28, "p_away": .22,
            "details": {
                "candidate_bets": [{"selection": "manual"}],
                "live_odds": {"obsolete": True},
                "market_candidates": [{"obsolete": True}],
            },
        }],
    }
    if forecast_generated_at is not None:
        payload["forecasts"][0]["forecast_generated_at"] = forecast_generated_at
    return payload


def _available_snapshot(record):
    return {
        "provider": "the_odds_api", "status": "available",
        "snapshot_at": record["received_at"], "records": [record],
        "quota": {"remaining": 499},
    }


def test_attaches_recent_prices_as_separate_shadow_candidates() -> None:
    record = normalize_odds_event(
        _event(), fixtures=[_fixture()], snapshot_at="2026-07-14T12:05:00Z"
    )
    result = apply_odds_snapshot_to_live_payload(
        _forecast_payload(), _available_snapshot(record),
        now="2026-07-14T12:10:00Z",
    )
    details = result["forecasts"][0]["details"]
    assert "live_odds" not in details
    assert details["candidate_bets"] == [{"selection": "manual"}]
    assert details["market_snapshot"]["best_1x2"]["home"]["odds"] == 1.8
    assert details["market_snapshot"]["captured_at_utc"] == "2026-07-14T12:05:00Z"
    assert details["market_snapshot"]["source_provider"] == "the_odds_api"
    assert details["market_snapshot"]["status"] == "SHADOW_ONLY"
    assert len(details["market_candidates"]) == 3
    assert all(row["status"] == "SHADOW_ONLY" for row in details["market_candidates"])
    assert all(row["source_provider"] == "the_odds_api" for row in details["market_candidates"])
    assert result["odds_feed"]["matched_forecasts"] == 1
    assert result["odds_feed"]["status"] == "SHADOW_ONLY"


def test_post_kickoff_capture_is_rejected() -> None:
    record = normalize_odds_event(
        _event(), fixtures=[_fixture()], snapshot_at="2026-07-14T15:00:00Z"
    )
    result = apply_odds_snapshot_to_live_payload(
        _forecast_payload(), _available_snapshot(record),
        now="2026-07-14T15:01:00Z",
    )
    details = result["forecasts"][0]["details"]
    assert details["market_snapshot"]["status"] == "REJECTED"
    assert details["market_snapshot"]["reason"] == "captured_at_or_after_kickoff"
    assert details["market_candidates"] == []


def test_snapshot_older_than_two_hours_is_marked_stale() -> None:
    record = normalize_odds_event(
        _event(), fixtures=[_fixture()], snapshot_at="2026-07-14T12:05:00Z"
    )
    result = apply_odds_snapshot_to_live_payload(
        _forecast_payload(), _available_snapshot(record),
        now="2026-07-14T14:05:01Z",
    )
    snapshot = result["forecasts"][0]["details"]["market_snapshot"]
    assert snapshot["status"] == "STALE"
    assert snapshot["reason"] == "older_than_ttl"
    assert result["odds_feed"]["stale_forecasts"] == 1


def test_snapshot_before_forecast_generation_is_rejected() -> None:
    record = normalize_odds_event(
        _event(), fixtures=[_fixture()], snapshot_at="2026-07-14T12:05:00Z"
    )
    result = apply_odds_snapshot_to_live_payload(
        _forecast_payload(generated_at="2026-07-14T12:06:00Z"),
        _available_snapshot(record), now="2026-07-14T12:10:00Z",
    )
    snapshot = result["forecasts"][0]["details"]["market_snapshot"]
    assert snapshot["status"] == "REJECTED"
    assert snapshot["reason"] == "captured_before_forecast"


def test_context_refresh_does_not_reject_quote_after_frozen_forecast() -> None:
    record = normalize_odds_event(
        _event(), fixtures=[_fixture()], snapshot_at="2026-07-14T12:05:00Z"
    )
    result = apply_odds_snapshot_to_live_payload(
        _forecast_payload(
            generated_at="2026-07-14T12:09:00Z",
            forecast_generated_at="2026-07-14T12:00:00Z",
        ),
        _available_snapshot(record),
        now="2026-07-14T12:10:00Z",
    )

    assert result["forecasts"][0]["details"]["market_snapshot"]["status"] == (
        "SHADOW_ONLY"
    )


def test_future_snapshot_is_rejected() -> None:
    record = normalize_odds_event(
        _event(), fixtures=[_fixture()], snapshot_at="2026-07-14T12:05:00Z"
    )
    result = apply_odds_snapshot_to_live_payload(
        _forecast_payload(), _available_snapshot(record),
        now="2026-07-14T12:04:59Z",
    )
    snapshot = result["forecasts"][0]["details"]["market_snapshot"]
    assert snapshot["status"] == "REJECTED"
    assert snapshot["reason"] == "captured_in_future"


def test_unavailable_refresh_clears_old_provider_fields_but_keeps_manual_bets() -> None:
    result = apply_odds_snapshot_to_live_payload(
        _forecast_payload(),
        {
            "provider": "the_odds_api", "status": "unavailable",
            "reason": "all_sport_requests_failed", "records": None,
        },
        now="2026-07-14T12:10:00Z",
    )
    details = result["forecasts"][0]["details"]
    assert details["candidate_bets"] == [{"selection": "manual"}]
    assert "live_odds" not in details
    assert "market_snapshot" not in details
    assert "market_candidates" not in details
    assert result["odds_feed"]["status"] == "UNAVAILABLE"


def test_legacy_provider_candidates_are_cleared_on_unavailable_refresh() -> None:
    payload = _forecast_payload()
    payload["forecasts"][0]["details"]["candidate_bets"] = [{
        "outcome": "home", "bookmaker_key": "pinnacle", "status": "SHADOW_ONLY",
    }]
    result = apply_odds_snapshot_to_live_payload(
        payload,
        {
            "provider": "the_odds_api", "status": "unavailable",
            "reason": "all_sport_requests_failed", "records": None,
        },
        now="2026-07-14T12:10:00Z",
    )
    assert "candidate_bets" not in result["forecasts"][0]["details"]


def test_rolling_merge_keeps_latest_fixture_and_per_sport_poll_times() -> None:
    first = normalize_odds_event(
        _event(), fixtures=[_fixture()], snapshot_at="2026-07-14T12:00:00Z"
    )
    other = deepcopy(first)
    other.update({
        "fixture_id": "other", "provider_event_id": "other-event",
        "sport_key": "sport-b", "snapshot_at": "2026-07-14T11:00:00Z",
        "received_at": "2026-07-14T11:00:00Z",
    })
    newer = deepcopy(first)
    newer.update({
        "snapshot_at": "2026-07-14T12:30:00Z",
        "requested_at": "2026-07-14T12:29:58Z",
        "received_at": "2026-07-14T12:30:00Z",
    })
    previous = {
        "provider": "the_odds_api", "status": "available",
        "snapshot_at": "2026-07-14T12:00:00Z", "records": [first, other],
        "sport_poll_times": {
            "sport-a": {"requested_at": "2026-07-14T11:59:58Z", "received_at": "2026-07-14T12:00:00Z", "status": "available"},
            "sport-b": {"requested_at": "2026-07-14T10:59:58Z", "received_at": "2026-07-14T11:00:00Z", "status": "available"},
        },
    }
    current = {
        "provider": "the_odds_api", "status": "available",
        "snapshot_at": "2026-07-14T12:30:00Z", "records": [newer],
        "sport_poll_times": {
            "sport-a": {"requested_at": "2026-07-14T12:29:58Z", "received_at": "2026-07-14T12:30:00Z", "status": "available"},
        },
    }
    merged = merge_odds_snapshots(previous, current)
    assert merged["snapshot_at"] == "2026-07-14T12:30:00Z"
    assert len(merged["records"]) == 2
    assert {row["fixture_id"]: row["received_at"] for row in merged["records"]} == {
        "2048641": "2026-07-14T12:30:00Z",
        "other": "2026-07-14T11:00:00Z",
    }
    assert merged["sport_poll_times"]["sport-a"]["received_at"] == "2026-07-14T12:30:00Z"
    assert merged["sport_poll_times"]["sport-b"]["received_at"] == "2026-07-14T11:00:00Z"


def test_rolling_merge_retains_records_through_one_failed_sport_poll() -> None:
    record = normalize_odds_event(
        _event(), fixtures=[_fixture()], snapshot_at="2026-07-14T12:00:00Z"
    )
    previous = {
        "provider": "the_odds_api", "status": "available",
        "snapshot_at": "2026-07-14T12:00:00Z", "records": [record],
        "sport_poll_times": {},
    }
    current = {
        "provider": "the_odds_api", "status": "unavailable",
        "reason": "all_sport_requests_failed", "snapshot_at": "2026-07-14T12:30:00Z",
        "records": None, "requested_sport_keys": ["sport-b"],
        "sport_poll_times": {
            "sport-b": {
                "requested_at": "2026-07-14T12:29:58Z",
                "received_at": "2026-07-14T12:30:00Z", "status": "unavailable",
            },
        },
    }
    merged = merge_odds_snapshots(previous, current)
    assert merged["status"] == "available"
    assert merged["records"] == [record]
    assert merged["snapshot_at"] == "2026-07-14T12:30:00Z"
    assert merged["requested_sport_keys"] == ["sport-b"]


def test_http_errors_never_persist_api_key_or_request_url() -> None:
    result = TheOddsApiProvider(api_key="top-secret", session=ErrorSession()).fetch_snapshot(
        sport_keys=["soccer_uefa_champs_league"], fixtures=[_fixture()],
        snapshot_at="2026-07-14T12:05:00Z",
    )
    assert result["status"] == "unavailable"
    assert result["errors"][0]["error"] == "HTTPError: status=401"
    assert "top-secret" not in str(result)
    assert "apiKey" not in str(result)
