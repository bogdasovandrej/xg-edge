from __future__ import annotations

from copy import deepcopy
import json

import pytest

from xgedge.evaluation.prospective import (
    CONFIRMATORY_HORIZON,
    POLICY_HASH,
    SCHEMA_VERSION,
    apply_summary_to_live_payload,
    finalize_clv_after_kickoff,
    ingest_odds_snapshot,
    new_ledger,
    prospective_summary,
    settle_results,
)


def _fixture(
    fixture_id: str = "m1",
    *,
    competition: str = "Test League",
    kickoff: str = "2026-07-14T15:00:00Z",
) -> dict:
    return {
        "id": fixture_id,
        "competition": competition,
        "home": f"Home {fixture_id}",
        "away": f"Away {fixture_id}",
        "kickoff_utc": kickoff,
    }


def _forecast(
    fixture_id: str = "m1",
    *,
    competition: str = "Test League",
    model: str = "test-model-v1",
    probability_basis: str = "market_anchored",
) -> dict:
    return {
        "id": fixture_id,
        "competition": competition,
        "model": model,
        "probability_basis": probability_basis,
        "p_home": 0.50,
        "p_draw": 0.25,
        "p_away": 0.25,
    }


def test_ingest_preserves_per_forecast_generation_time_after_payload_refresh() -> None:
    forecast = _forecast()
    forecast["forecast_generated_at"] = "2026-07-14T11:30:00Z"
    payload = _payload(forecast)
    payload["generated_at"] = "2026-07-14T12:10:00Z"

    ledger = ingest_odds_snapshot(
        None,
        _snapshot("2026-07-14T12:15:00Z", (2.20, 4.0, 4.0)),
        fixtures=[_fixture()],
        live_payload=payload,
    )

    assert ledger["fixtures"]["m1"]["forecast"]["generated_at"] == (
        "2026-07-14T11:30:00Z"
    )


def _payload(*forecasts: dict) -> dict:
    return {
        "generated_at": "2026-07-14T12:00:00Z",
        "betting_gate": {"allowed": False},
        "forecasts": list(forecasts or (_forecast(),)),
    }


def _snapshot(
    at: str,
    odds: tuple[float, float, float],
    *,
    fixture_id: str = "m1",
    bookmaker: str = "pinnacle",
) -> dict:
    return {
        "provider": "the_odds_api",
        "status": "available",
        "reason": None,
        "snapshot_at": at,
        "records": [
            {
                "fixture_id": fixture_id,
                "provider_event_id": f"event-{fixture_id}",
                "sport_key": "soccer_test",
                "snapshot_at": at,
                "bookmakers": [
                    {
                        "key": bookmaker,
                        "title": bookmaker.title(),
                        "last_update": at,
                        "markets": {
                            "h2h": {
                                "home": odds[0],
                                "draw": odds[1],
                                "away": odds[2],
                            }
                        },
                    }
                ],
            }
        ],
    }


def _provisional_ledger(values: list[float]) -> tuple[dict, str]:
    ledger = ingest_odds_snapshot(
        None,
        _snapshot("2026-07-14T12:00:00Z", (2.20, 4.0, 4.0)),
        fixtures=[_fixture()],
        live_payload=_payload(),
    )
    ledger = ingest_odds_snapshot(
        ledger,
        _snapshot("2026-07-14T14:50:00Z", (2.0, 4.0, 4.0)),
        fixtures=[_fixture()],
        live_payload=_payload(),
    )
    template = deepcopy(ledger["fixtures"]["m1"])
    cohort_id = template["evaluation_cohort_id"]
    ledger["fixtures"] = {}
    for index, value in enumerate(values):
        fixture_id = f"{index:03d}"
        entry = deepcopy(template)
        entry["fixture_id"] = fixture_id
        entry["provider_event_id"] = f"event-{fixture_id}"
        entry["clv"]["value"] = value
        ledger["fixtures"][fixture_id] = entry
    ledger["gate"] = prospective_summary(ledger)
    return ledger, cohort_id


def test_schema_12_freezes_the_full_confirmatory_policy() -> None:
    ledger = new_ledger(updated_at="2026-07-14T12:00:00Z")
    assert ledger["schema_version"] == SCHEMA_VERSION == "prospective-clv/1.2"
    assert ledger["policy_hash"] == POLICY_HASH
    assert ledger["policy"] == {
        "market": "1X2",
        "taken_benchmark": "pinnacle",
        "closing_benchmark": "pinnacle",
        "region": "eu",
        "odds_format": "decimal",
        "commission": "none",
        "edge_threshold": 0.03,
        "max_odds": 6.0,
        "closing_window_minutes": 60,
        "confirmatory_horizon": 100,
        "ordering": ["kickoff_utc", "fixture_id"],
        "decision_rule": "one_shot_95pct_cluster_bootstrap_lower_ci_above_zero",
    }
    tampered = deepcopy(ledger)
    tampered["policy"]["edge_threshold"] = 0.04
    with pytest.raises(ValueError, match="immutable"):
        prospective_summary(tampered)
    with pytest.raises(ValueError, match="frozen"):
        ingest_odds_snapshot(
            ledger,
            _snapshot("2026-07-14T12:00:00Z", (2.2, 4.0, 4.0)),
            fixtures=[_fixture()],
            live_payload=_payload(),
            edge_threshold=0.04,
        )


def test_confirmatory_close_is_provisional_before_kickoff_and_ready_after() -> None:
    ledger = ingest_odds_snapshot(
        None,
        _snapshot("2026-07-14T12:00:00Z", (2.20, 4.0, 4.0)),
        fixtures=[_fixture()],
        live_payload=_payload(),
    )
    ledger = ingest_odds_snapshot(
        ledger,
        _snapshot("2026-07-14T14:50:00Z", (2.0, 4.0, 4.0)),
        fixtures=[_fixture()],
        live_payload=_payload(),
    )
    entry = ledger["fixtures"]["m1"]
    cohort_id = entry["evaluation_cohort_id"]
    assert entry["shadow_candidate"]["bookmaker_key"] == "pinnacle"
    assert entry["shadow_candidate"]["taken_odds"] == pytest.approx(2.2)
    assert entry["closing"]["evaluation_tier"] == "confirmatory"
    assert entry["clv"]["value"] == pytest.approx(0.10)
    assert entry["clv"]["status"] == "provisional"
    assert ledger["gate"]["cohorts"][cohort_id]["confirmatory_ready"] == 0

    before = finalize_clv_after_kickoff(
        ledger, finalized_at="2026-07-14T14:59:59Z"
    )
    assert before["fixtures"]["m1"]["clv"]["status"] == "provisional"
    after = finalize_clv_after_kickoff(
        ledger, finalized_at="2026-07-14T15:00:01Z"
    )
    assert after["fixtures"]["m1"]["clv"]["status"] == "ready"
    cohort = after["gate"]["cohorts"][cohort_id]
    assert cohort["confirmatory_ready"] == 1
    assert cohort["clv"]["mean"] is None  # no interim inferential peek
    assert cohort["action"] == "NO BET"


def test_non_pinnacle_taken_price_is_not_a_confirmatory_candidate() -> None:
    ledger = ingest_odds_snapshot(
        None,
        _snapshot(
            "2026-07-14T12:00:00Z", (2.5, 4.0, 4.0), bookmaker="betfair"
        ),
        fixtures=[_fixture()],
        live_payload=_payload(),
    )
    assert ledger["fixtures"]["m1"]["shadow_candidate"] is None


def test_non_pinnacle_close_stays_diagnostic_and_never_enters_horizon() -> None:
    ledger = ingest_odds_snapshot(
        None,
        _snapshot("2026-07-14T12:00:00Z", (2.20, 4.0, 4.0)),
        fixtures=[_fixture()],
        live_payload=_payload(),
    )
    ledger = ingest_odds_snapshot(
        ledger,
        _snapshot(
            "2026-07-14T14:50:00Z", (2.0, 4.0, 4.0), bookmaker="betfair"
        ),
        fixtures=[_fixture()],
        live_payload=_payload(),
    )
    entry = ledger["fixtures"]["m1"]
    cohort_id = entry["evaluation_cohort_id"]
    assert entry["clv"]["status"] == "provisional"
    assert entry["clv"]["evaluation_tier"] == "diagnostic"
    finalized = finalize_clv_after_kickoff(
        ledger, finalized_at="2026-07-14T15:00:01Z"
    )
    assert finalized["fixtures"]["m1"]["clv"]["status"] == "diagnostic"
    cohort = finalized["gate"]["cohorts"][cohort_id]
    assert cohort["confirmatory_ready"] == 0
    assert cohort["diagnostic_closes"] == 1
    assert cohort["decision"]["status"] == "pending"


def test_duplicate_snapshot_is_idempotent_and_post_kickoff_is_ignored() -> None:
    ledger = ingest_odds_snapshot(
        None,
        _snapshot("2026-07-14T12:00:00Z", (2.2, 4.0, 4.0)),
        fixtures=[_fixture()],
        live_payload=_payload(),
    )
    ledger = ingest_odds_snapshot(
        ledger,
        _snapshot("2026-07-14T12:00:00Z", (2.2, 4.0, 4.0)),
        fixtures=[_fixture()],
        live_payload=_payload(),
    )
    assert len(ledger["fixtures"]["m1"]["observations"]) == 1
    later = ingest_odds_snapshot(
        ledger,
        _snapshot("2026-07-14T15:01:00Z", (1.8, 4.5, 5.0)),
        fixtures=[_fixture()],
        live_payload=_payload(),
    )
    assert len(later["fixtures"]["m1"]["observations"]) == 1


def test_different_models_are_never_mixed_into_one_cohort_or_global_gate() -> None:
    fixtures = [_fixture("a"), _fixture("b")]
    payload = _payload(
        _forecast("a", model="model-a"),
        _forecast("b", model="model-b"),
    )
    ledger = ingest_odds_snapshot(
        None,
        _snapshot("2026-07-14T12:00:00Z", (2.2, 4.0, 4.0), fixture_id="a"),
        fixtures=fixtures,
        live_payload=payload,
    )
    ledger = ingest_odds_snapshot(
        ledger,
        _snapshot("2026-07-14T12:01:00Z", (2.2, 4.0, 4.0), fixture_id="b"),
        fixtures=fixtures,
        live_payload=payload,
    )
    ledger = ingest_odds_snapshot(
        ledger,
        _snapshot("2026-07-14T14:49:00Z", (2.0, 4.0, 4.0), fixture_id="a"),
        fixtures=fixtures,
        live_payload=payload,
    )
    ledger = ingest_odds_snapshot(
        ledger,
        _snapshot("2026-07-14T14:50:00Z", (2.0, 4.0, 4.0), fixture_id="b"),
        fixtures=fixtures,
        live_payload=payload,
    )
    ledger = finalize_clv_after_kickoff(
        ledger, finalized_at="2026-07-14T15:00:01Z"
    )
    cohort_ids = {
        ledger["fixtures"][fixture_id]["evaluation_cohort_id"]
        for fixture_id in ("a", "b")
    }
    assert len(cohort_ids) == 2
    assert ledger["gate"]["cohort_count"] == 2
    assert {row["confirmatory_ready"] for row in ledger["gate"]["cohorts"].values()} == {1}
    assert ledger["gate"]["action"] == "NO BET"
    assert ledger["gate"]["clv"]["n"] == 0


@pytest.mark.parametrize(
    ("value", "expected_status", "expected_action"),
    [(0.05, "pass", "BET"), (-0.05, "fail", "NO BET")],
)
def test_fixed_one_shot_decision_locks_on_first_hundred_ready_in_kickoff_order(
    value: float, expected_status: str, expected_action: str
) -> None:
    ledger, cohort_id = _provisional_ledger([value] * CONFIRMATORY_HORIZON)
    finalized = finalize_clv_after_kickoff(
        ledger, finalized_at="2026-07-14T15:00:01Z"
    )
    decision = finalized["cohorts"][cohort_id]["decision"]
    assert decision["locked"] is True
    assert decision["status"] == expected_status
    assert decision["action"] == expected_action
    assert decision["fixture_ids"] == [f"{index:03d}" for index in range(100)]
    assert decision["clv"]["n"] == 100


def test_locked_decision_is_not_optionally_re_evaluated_by_later_evidence() -> None:
    ledger, cohort_id = _provisional_ledger([0.05] * CONFIRMATORY_HORIZON)
    locked = finalize_clv_after_kickoff(
        ledger, finalized_at="2026-07-14T15:00:01Z"
    )
    frozen_decision = deepcopy(locked["cohorts"][cohort_id]["decision"])

    later = deepcopy(locked["fixtures"]["099"])
    later["fixture_id"] = "zzz-later"
    later["kickoff_utc"] = "2026-07-14T16:00:00Z"
    later["closing"]["snapshot_at"] = "2026-07-14T15:50:00Z"
    later["clv"]["value"] = -0.99
    later["clv"]["status"] = "provisional"
    later["clv"].pop("finalized_at", None)
    later["clv"].pop("seconds_before_kickoff", None)
    locked["fixtures"]["zzz-later"] = later
    finalized_again = finalize_clv_after_kickoff(
        locked, finalized_at="2026-07-14T16:00:01Z"
    )
    assert finalized_again["cohorts"][cohort_id]["decision"] == frozen_decision
    cohort = finalized_again["gate"]["cohorts"][cohort_id]
    assert cohort["action"] == "BET"
    assert cohort["clv"]["mean"] == pytest.approx(0.05)
    assert cohort["post_horizon_ready"] == 1


def test_apply_summary_sets_per_forecast_cohort_gate_but_global_gate_is_false() -> None:
    ledger, cohort_id = _provisional_ledger([0.05] * CONFIRMATORY_HORIZON)
    ledger = finalize_clv_after_kickoff(
        ledger, finalized_at="2026-07-14T15:00:01Z"
    )
    public = apply_summary_to_live_payload(_payload(_forecast("000")), ledger["gate"])
    assert public["betting_gate"] == {
        "allowed": False,
        "action": "NO BET",
        "reason": "global_gate_disabled_cohort_specific_only",
    }
    forecast = public["forecasts"][0]
    assert forecast["evaluation_cohort_id"] == cohort_id
    assert forecast["cohort_gate"]["allowed"] is True
    assert forecast["cohort_gate"]["decision_status"] == "pass"


def test_results_are_calibrated_inside_their_own_cohort_only() -> None:
    ledger = ingest_odds_snapshot(
        None,
        _snapshot("2026-07-14T12:00:00Z", (2.2, 4.0, 4.0)),
        fixtures=[_fixture()],
        live_payload=_payload(),
    )
    settled = settle_results(
        ledger,
        [{"id": "m1", "status": "FINISHED", "home_goals_90": 1, "away_goals_90": 0}],
        settled_at="2026-07-14T17:00:00Z",
    )
    cohort_id = settled["fixtures"]["m1"]["evaluation_cohort_id"]
    assert settled["fixtures"]["m1"]["result"]["outcome"] == "home"
    assert settled["gate"]["cohorts"][cohort_id]["calibration"]["n"] == 1
    # A single cohort may be mirrored for legacy readers; heterogeneous
    # cohorts are never pooled (covered by the mixed-model test above).
    assert settled["gate"]["calibration"]["n"] == 1


def test_every_public_summary_is_strict_json_without_nan() -> None:
    ledger = new_ledger(updated_at="2026-07-14T12:00:00Z")
    summary = prospective_summary(ledger)
    assert summary["clv"]["mean"] is None
    assert summary["clv"]["ci_low"] is None
    json.dumps(ledger, allow_nan=False)
    json.dumps(summary, allow_nan=False)
