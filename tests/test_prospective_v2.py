from copy import deepcopy
import json
from pathlib import Path

import pytest

from xgedge.evaluation.prospective_v2 import (
    POLICY,
    POLICY_HASH,
    SCHEMA_VERSION,
    audit_capture_health,
    benchmark_probabilities,
    ingest_snapshot,
    new_ledger,
    validate_ledger,
)


def _forecast(**overrides):
    row = {
        "id": "m1",
        "competition": "Premier League",
        "model": "market-residual-v1",
        "probability_basis": "betfair_exchange_residual",
        "forecast_generated_at": "2026-08-01T12:00:00Z",
        "kickoff_utc": "2026-08-01T15:00:00Z",
        "p_home": 0.55,
        "p_draw": 0.25,
        "p_away": 0.20,
    }
    row.update(overrides)
    return row


def _payload(*forecasts):
    return {
        "generated_at": "2026-08-01T12:02:00Z",
        "forecasts": list(forecasts or [_forecast()]),
    }


def _fixtures():
    return [{
        "id": "m1",
        "competition": "Premier League",
        "kickoff_utc": "2026-08-01T15:00:00Z",
    }]


def _snapshot(at="2026-08-01T12:02:00Z", *, include_sharp=True):
    books = [
        {
            "key": "bet365",
            "title": "Bet365",
            "markets": {"h2h": {"home": 2.10, "draw": 3.8, "away": 4.2}},
        }
    ]
    if include_sharp:
        books.append({
            "key": "betfair_exchange",
            "title": "Betfair Exchange",
            "liquidity_verified": True,
            "markets": {"h2h": {"home": 2.0, "draw": 3.9, "away": 4.5}},
        })
    return {
        "provider": "odds_api_io",
        "status": "available",
        "snapshot_at": at,
        "records": [{
            "fixture_id": "m1",
            "received_at": at,
            "bookmakers": books,
        }],
    }


def test_v2_policy_is_frozen_and_separate_from_v1():
    ledger = new_ledger(updated_at="2026-08-01T12:00:00Z")
    assert ledger["schema_version"] == SCHEMA_VERSION == "prospective-clv/2.0"
    assert ledger["policy_hash"] == POLICY_HASH
    assert ledger["policy"] == POLICY
    assert ledger["policy"]["sharp_benchmark"]["bookmaker_key"] == "betfair_exchange"
    with pytest.raises(ValueError, match="own ledger"):
        validate_ledger({"schema_version": "prospective-clv/1.2"})
    tampered = deepcopy(ledger)
    tampered["policy"]["promotion"]["primary_horizon"] = 100
    with pytest.raises(ValueError, match="immutable"):
        validate_ledger(tampered)

    checked_in = json.loads(
        Path("reports/live/prospective_clv_v2.json").read_text(encoding="utf-8")
    )
    validate_ledger(checked_in)


def test_betfair_close_is_commission_adjusted_and_devigged():
    probabilities = benchmark_probabilities(_snapshot()["records"][0])
    assert probabilities is not None
    assert sum(probabilities.values()) == pytest.approx(1.0)
    assert all(0 < value < 1 for value in probabilities.values())


def test_initial_capture_freezes_forecast_and_uses_lcb95_candidate():
    ledger = ingest_snapshot(
        None, _snapshot(), fixtures=_fixtures(), live_payload=_payload()
    )
    entry = ledger["fixtures"]["m1"]
    assert entry["initial_capture_at"] == "2026-08-01T12:02:00Z"
    assert entry["candidate"]["status"] == "PAPER_ONLY"
    assert entry["candidate"]["real_money_eligible"] is False
    assert entry["candidate"]["ev_lcb95"] > 0
    assert entry["candidate"]["stake_policy"]["maximum_bankroll_fraction"] == 0.0025
    assert entry["candidate"]["stake_policy"]["kelly_allowed"] is False

    changed = _payload(_forecast(p_home=0.56, p_draw=0.24))
    updated = ingest_snapshot(
        ledger,
        _snapshot("2026-08-01T12:04:00Z"),
        fixtures=_fixtures(),
        live_payload=changed,
    )
    assert len(updated["fixtures"]["m1"]["observations"]) == 1
    assert updated["rejections"]["forecast_changed_after_freeze"] == 1


def test_missing_sharp_feed_and_missed_initial_window_fail_closed():
    without_sharp = ingest_snapshot(
        None,
        _snapshot(include_sharp=False),
        fixtures=_fixtures(),
        live_payload=_payload(),
    )
    assert without_sharp["fixtures"] == {}
    assert without_sharp["rejections"]["sharp_benchmark_missing"] == 1

    late = ingest_snapshot(
        None,
        _snapshot("2026-08-01T12:06:00Z"),
        fixtures=_fixtures(),
        live_payload=_payload(),
    )
    assert late["fixtures"] == {}
    assert late["rejections"]["initial_capture_window_missed"] == 1

    no_liquidity = _snapshot()
    no_liquidity["records"][0]["bookmakers"][1].pop("liquidity_verified")
    rejected = ingest_snapshot(
        None, no_liquidity, fixtures=_fixtures(), live_payload=_payload()
    )
    assert rejected["fixtures"] == {}
    assert rejected["rejections"]["sharp_benchmark_missing"] == 1


def test_only_stable_top5_market_residual_cohort_is_admitted():
    for changed in (
        _forecast(competition="UEFA Champions League"),
        _forecast(model="Hybrid Elo–Poisson (experimental)"),
        _forecast(probability_basis="clubelo"),
    ):
        ledger = ingest_snapshot(
            None, _snapshot(), fixtures=_fixtures(), live_payload=_payload(changed)
        )
        assert ledger["fixtures"] == {}
        assert sum(ledger["rejections"].values()) == 1


def test_closing_window_is_finalized_after_kickoff_and_interim_mean_is_hidden():
    ledger = ingest_snapshot(
        None, _snapshot(), fixtures=_fixtures(), live_payload=_payload()
    )
    close = _snapshot("2026-08-01T14:50:00Z")
    ledger = ingest_snapshot(
        ledger, close, fixtures=_fixtures(), live_payload=_payload()
    )
    finalized = audit_capture_health(
        ledger,
        live_payload=_payload(),
        now="2026-08-01T15:00:01Z",
    )
    assert finalized["fixtures"]["m1"]["closing"]["captured_at"] == (
        "2026-08-01T14:50:00Z"
    )
    assert finalized["fixtures"]["m1"]["clv"]["status"] == "ready"
    assert finalized["monitor"]["confirmatory_ready"] == 1
    assert finalized["monitor"]["mean_clv"] is None
    assert finalized["monitor"]["interim_inference_hidden"] is True
    assert finalized["gate"]["allowed"] is False


def test_alerts_expose_missed_initial_and_closing_windows():
    initial_missed = audit_capture_health(
        new_ledger(updated_at="2026-08-01T12:00:00Z"),
        live_payload=_payload(),
        now="2026-08-01T12:05:01Z",
    )
    assert initial_missed["alerts"][0]["kind"] == "initial_capture_missed"

    ledger = ingest_snapshot(
        None, _snapshot(), fixtures=_fixtures(), live_payload=_payload()
    )
    closing_missed = audit_capture_health(
        ledger,
        live_payload=_payload(),
        now="2026-08-01T15:00:01Z",
    )
    assert closing_missed["alerts"][0]["kind"] == "closing_capture_missed"
