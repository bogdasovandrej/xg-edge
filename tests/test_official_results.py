"""Official result settlement tests; network access is forbidden."""
from __future__ import annotations

import json

import pytest
import requests

from scripts.settle_prospective_results import settle_files
from xgedge.data.official_results import fetch_tracked_results, normalize_uefa_result
from xgedge.evaluation.prospective import new_ledger


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
        raise AssertionError("network access is forbidden in result tests")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


def _entry(fixture_id: str, sport_key: str, **overrides):
    row = {
        "fixture_id": fixture_id,
        "kickoff_utc": "2026-07-14T15:00:00Z",
        "sport_key": sport_key,
        "forecast": {"probabilities": {"home": .5, "draw": .3, "away": .2}},
        "result": None,
        "calibration": None,
        "clv": None,
        "shadow_candidate": None,
    }
    row.update(overrides)
    return row


def _uefa(match_id="ucl-1", status="FINISHED"):
    return {
        "id": match_id,
        "status": status,
        "score": {
            "regular": {"home": 2, "away": 1},
            "total": {"home": 3, "away": 2},
        },
    }


def test_uefa_result_uses_regulation_score_and_rejects_wrong_identity() -> None:
    result = normalize_uefa_result(_uefa(), expected_id="ucl-1")
    assert (result["home_goals_90"], result["away_goals_90"]) == (2, 1)
    assert normalize_uefa_result(_uefa(status="LIVE"), expected_id="ucl-1") is None
    with pytest.raises(ValueError, match="does not match"):
        normalize_uefa_result(_uefa(), expected_id="different")
    malformed = _uefa()
    malformed["score"].pop("regular")
    with pytest.raises(ValueError, match="regulation-time"):
        normalize_uefa_result(malformed)


def test_fetches_only_past_unsettled_tracked_fifa_and_uefa_ids() -> None:
    ledger = new_ledger(updated_at="2026-07-14T16:00:00Z")
    ledger["fixtures"] = {
        "wc-1": _entry("wc-1", "soccer_fifa_world_cup"),
        "ucl-1": _entry("ucl-1", "soccer_uefa_champs_league"),
        "future": _entry(
            "future", "soccer_uefa_champs_league", kickoff_utc="2026-07-15T15:00:00Z"
        ),
        "done": _entry(
            "done", "soccer_fifa_world_cup", result={"outcome": "draw"}
        ),
        "unknown": _entry("unknown", "other_sport"),
    }
    fifa_calls = []

    def fifa_loader(**kwargs):
        fifa_calls.append(kwargs)
        return {"matches": [
            {
                "id": "wc-1", "status": "FINISHED",
                "home_goals_90": 1, "away_goals_90": 1,
            },
            {
                "id": "done", "status": "FINISHED",
                "home_goals_90": 9, "away_goals_90": 9,
            },
        ]}

    session = FakeSession([_uefa()])
    snapshot = fetch_tracked_results(
        ledger,
        now="2026-07-14T18:00:00Z",
        fifa_loader=fifa_loader,
        session=session,
        uefa_match_url="https://uefa.test/{match_id}",
    )

    assert snapshot["status"] == "available"
    assert snapshot["requested_fixture_ids"] == ["ucl-1", "wc-1"]
    assert [(row["source"], row["id"]) for row in snapshot["results"]] == [
        ("fifa", "wc-1"), ("uefa", "ucl-1")
    ]
    assert len(fifa_calls) == 1
    assert [call[0] for call in session.calls] == ["https://uefa.test/ucl-1"]


def test_one_official_feed_failure_is_partial_and_does_not_block_the_other() -> None:
    ledger = new_ledger(updated_at="2026-07-14T16:00:00Z")
    ledger["fixtures"] = {
        "wc-1": _entry("wc-1", "soccer_fifa_world_cup"),
        "ucl-1": _entry("ucl-1", "soccer_uefa_champs_league"),
    }

    def broken_fifa(**kwargs):
        raise requests.ConnectionError("temporary FIFA error")

    snapshot = fetch_tracked_results(
        ledger,
        now="2026-07-14T18:00:00Z",
        fifa_loader=broken_fifa,
        session=FakeSession([_uefa()]),
    )

    assert snapshot["status"] == "partial"
    assert [row["id"] for row in snapshot["results"]] == ["ucl-1"]
    assert snapshot["errors"][0]["source"] == "fifa"


def test_official_feed_error_returns_unavailable_instead_of_raising() -> None:
    ledger = new_ledger(updated_at="2026-07-14T16:00:00Z")
    ledger["fixtures"]["ucl-1"] = _entry(
        "ucl-1", "soccer_uefa_champs_league"
    )

    class BrokenSession:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError("temporary UEFA error")

    snapshot = fetch_tracked_results(
        ledger, now="2026-07-14T18:00:00Z", session=BrokenSession()
    )
    assert snapshot["status"] == "unavailable"
    assert snapshot["results"] == []
    assert snapshot["errors"][0]["fixture_id"] == "ucl-1"


def test_cli_settles_calibration_updates_public_summary_and_preserves_clv(tmp_path) -> None:
    ledger_path = tmp_path / "prospective.json"
    live_path = tmp_path / "live.json"
    ledger = new_ledger(updated_at="2026-07-14T16:00:00Z")
    ledger["fixtures"]["ucl-1"] = _entry(
        "ucl-1",
        "soccer_uefa_champs_league",
        clv={"status": "ready", "value": .04},
        shadow_candidate={"selection": "home"},
    )
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    live_path.write_text(json.dumps({
        "generated_at": "2026-07-14T16:00:00Z",
        "betting_gate": {"allowed": False},
        "forecasts": [],
    }), encoding="utf-8")

    def fetcher(*args, **kwargs):
        return {
            "status": "available", "requested_fixture_ids": ["ucl-1"], "errors": [],
            "results": [{
                "id": "ucl-1", "status": "FINISHED",
                "home_goals_90": 2, "away_goals_90": 0,
            }],
        }

    result = settle_files(
        ledger_path, live_path, now="2026-07-14T18:00:00Z", fetcher=fetcher
    )
    stored = json.loads(ledger_path.read_text(encoding="utf-8"))
    public = json.loads(live_path.read_text(encoding="utf-8"))

    assert result["settled"] == 1
    assert stored["fixtures"]["ucl-1"]["result"]["outcome"] == "home"
    assert stored["fixtures"]["ucl-1"]["clv"] == {"status": "ready", "value": .04}
    assert stored["gate"]["clv"]["n"] == 1
    assert stored["gate"]["calibration"]["n"] == 1
    assert public["prospective_clv"]["calibration"]["n"] == 1
    assert public["betting_gate"]["allowed"] is False


def test_cli_missing_ledger_is_a_safe_noop(tmp_path) -> None:
    live_path = tmp_path / "live.json"
    live_path.write_text("{}", encoding="utf-8")
    result = settle_files(tmp_path / "missing.json", live_path)
    assert result == {"status": "skipped", "reason": "ledger_missing", "settled": 0}
    assert json.loads(live_path.read_text(encoding="utf-8")) == {}
