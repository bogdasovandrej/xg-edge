"""The self-audit must catch a wrong published number, not just pass."""
from __future__ import annotations

import copy

import pytest

from xgedge.evaluation.self_audit import audit_payload, suppress_unsafe_fixtures


def _payload() -> dict:
    """A small but internally consistent payload."""
    return {
        "generated_at": "2026-08-20T12:00:00Z",
        "forecasts": [{
            "id": "fx1",
            "kickoff_utc": "2026-08-25T18:00:00Z",
            "forecast_generated_at": "2026-08-20T12:00:00Z",
            "model_market_forecasts": [{
                "label": "ТМ 2.5",
                "market": "totals",
                "central": {"win": 0.55, "push": 0.0, "loss": 0.45},
                "conservative": {"win": 0.51, "push": 0.0, "loss": 0.49},
                "theoretical_probability": 0.55,
                "conservative_probability": 0.51,
                "fair": 1.0 + 0.49 / 0.51,
                "min_entry": (1.0 + 0.49 / 0.51) * 1.08,
            }],
            "details": {
                "market_candidates": [{
                    "selection": "П1",
                    "probability": 0.55,
                    "market_odds": 2.10,
                    "value_pct": (0.55 * 2.10 - 1.0) * 100.0,
                    "quote_captured_at": "2026-08-25T16:00:00Z",
                }],
            },
        }],
    }


def _push_market_payload() -> dict:
    """A market that can push, where 1/p and 1 + L/W genuinely differ.

    With no push mass the two formulas are algebraically identical, so a
    push-blind implementation is invisible on such a market. Only an integer
    line can expose it.
    """
    payload = _payload()
    row = payload["forecasts"][0]["model_market_forecasts"][0]
    row["label"] = "ИТМ1 1"
    row["central"] = {"win": 0.2633, "push": 0.3513, "loss": 0.3854}
    row["conservative"] = {"win": 0.2373, "push": 0.3513, "loss": 0.4114}
    row["theoretical_probability"] = 0.2633
    row["conservative_probability"] = 0.2373
    row["fair"] = 1.0 + 0.4114 / 0.2373
    row["min_entry"] = row["fair"] * 1.08
    return payload


def test_a_consistent_payload_passes() -> None:
    audit = audit_payload(_payload())
    assert audit["status"] == "PASSED"
    assert audit["findings"] == []
    assert audit["checked_markets"] == 1
    assert audit["checked_candidates"] == 1


def test_states_that_do_not_sum_to_one_are_critical() -> None:
    payload = _payload()
    payload["forecasts"][0]["model_market_forecasts"][0]["central"]["loss"] = 0.60
    audit = audit_payload(payload)
    assert audit["status"] == "FAILED"
    assert audit["findings_by_check"]["states_do_not_sum_to_one"] == 1


def test_fair_that_disagrees_with_its_own_states_is_caught() -> None:
    """The 1/p regression: right formula in tests, wrong number published."""
    payload = _push_market_payload()
    row = payload["forecasts"][0]["model_market_forecasts"][0]
    row["fair"] = 1.0 / row["conservative"]["win"]  # push-blind 1/p
    row["min_entry"] = row["fair"] * 1.08
    audit = audit_payload(payload)
    assert audit["status"] == "FAILED"
    assert audit["findings_by_check"]["fair_disagrees_with_states"] == 1


def test_min_entry_below_fair_is_caught() -> None:
    payload = _payload()
    payload["forecasts"][0]["model_market_forecasts"][0]["min_entry"] = 1.0
    audit = audit_payload(payload)
    assert audit["findings_by_check"]["min_entry_not_above_fair"] == 1


def test_value_pct_copied_from_the_wrong_field_is_caught() -> None:
    payload = _payload()
    # A plausible refactor bug: value_pct filled from a human rating.
    payload["forecasts"][0]["details"]["market_candidates"][0]["value_pct"] = 8.1
    audit = audit_payload(payload)
    assert audit["status"] == "FAILED"
    assert audit["findings_by_check"]["value_pct_disagrees_with_price"] == 1


def test_post_kickoff_quote_is_critical() -> None:
    payload = _payload()
    payload["forecasts"][0]["details"]["market_candidates"][0][
        "quote_captured_at"
    ] = "2026-08-25T19:00:00Z"
    audit = audit_payload(payload)
    assert audit["findings_by_check"]["quote_captured_after_kickoff"] == 1


def test_forecast_generated_after_kickoff_is_critical() -> None:
    payload = _payload()
    payload["forecasts"][0]["forecast_generated_at"] = "2026-08-25T19:00:00Z"
    audit = audit_payload(payload)
    assert audit["findings_by_check"]["forecast_generated_after_kickoff"] == 1


def test_conservative_above_central_is_a_warning_not_a_block() -> None:
    payload = _payload()
    payload["forecasts"][0]["model_market_forecasts"][0]["conservative_probability"] = 0.90
    audit = audit_payload(payload)
    assert audit["status"] == "PASSED"  # warnings alone do not block
    assert audit["findings_by_check"]["conservative_above_central"] == 1


def test_source_gap_rows_are_not_audited_as_broken_prices() -> None:
    payload = _payload()
    payload["forecasts"][0]["model_market_forecasts"].append(
        {"label": "ИТБ1 1", "market": "team_totals", "status": "SOURCE_GAP"}
    )
    audit = audit_payload(payload)
    assert audit["status"] == "PASSED"
    assert audit["checked_markets"] == 1


def test_a_failing_fixture_loses_its_betting_surfaces() -> None:
    payload = _payload()
    payload["value_top"] = {"candidates": [
        {"fixture_id": "fx1", "value_pct": 12.0},
        {"fixture_id": "fx2", "value_pct": 9.0},
    ]}
    payload["forecasts"][0]["model_market_forecasts"][0]["min_entry"] = 1.0
    audit = audit_payload(payload)
    suppressed = suppress_unsafe_fixtures(copy.deepcopy(payload), audit)

    forecast = suppressed["forecasts"][0]
    assert forecast["betting_eligible"] is False
    assert forecast["value_verdict"]["status"] == "NO_BET_FAILED_SELF_AUDIT"
    assert forecast["details"]["market_candidates"] == []
    # Only the failing fixture is removed from the top; the other survives.
    assert [row["fixture_id"] for row in suppressed["value_top"]["candidates"]] == ["fx2"]


def test_a_clean_payload_is_left_untouched() -> None:
    payload = _payload()
    audit = audit_payload(payload)
    assert suppress_unsafe_fixtures(copy.deepcopy(payload), audit) == payload


def test_the_audit_never_repairs_the_number_it_flags() -> None:
    """A silent fix would hide the defect and keep shipping it."""
    payload = _push_market_payload()
    broken = 1.0 / payload["forecasts"][0]["model_market_forecasts"][0]["conservative"]["win"]
    payload["forecasts"][0]["model_market_forecasts"][0]["fair"] = broken
    audit = audit_payload(payload)
    suppressed = suppress_unsafe_fixtures(payload, audit)
    assert suppressed["forecasts"][0]["model_market_forecasts"][0]["fair"] == broken
    assert audit["critical_count"] >= 1
