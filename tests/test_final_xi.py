"""Final XI and price gates before a candidate can enter the portfolio."""
from __future__ import annotations

from xgedge.research.final_xi import (
    ConfirmedLineup,
    LineupAssumption,
    evaluate_final_price,
    evaluate_final_xi,
    finalize_candidate,
)


def test_unconfirmed_lineup_stays_waiting() -> None:
    result = evaluate_final_xi(LineupAssumption(), ConfirmedLineup(confirmed=False))
    assert result["status"] == "WAITING_XI"


def test_confirmed_lineup_matching_assumption_passes() -> None:
    assumption = LineupAssumption(
        assumed_available_player_ids=frozenset({"p1", "p2"}),
        assumed_key_roles_available=frozenset({"main_striker"}),
        assumed_formation="4-3-3",
    )
    confirmed = ConfirmedLineup(confirmed=True, formation="4-3-3")
    result = evaluate_final_xi(assumption, confirmed)
    assert result["status"] == "FINAL_CHECK_PASSED"
    assert result["material_change"] is False


def test_losing_an_assumed_key_player_fails_the_gate() -> None:
    assumption = LineupAssumption(assumed_available_player_ids=frozenset({"p1"}))
    confirmed = ConfirmedLineup(confirmed=True, confirmed_out_player_ids=frozenset({"p1"}))
    result = evaluate_final_xi(assumption, confirmed)
    assert result["status"] == "FINAL_CHECK_FAILED"
    assert "assumed_available_player_confirmed_out" in result["reasons"]
    assert result["requires_new_probability"] is True


def test_missing_material_role_fails_the_gate() -> None:
    assumption = LineupAssumption(assumed_key_roles_available=frozenset({"goalkeeper"}))
    confirmed = ConfirmedLineup(confirmed=True, missing_key_roles=frozenset({"goalkeeper"}))
    assert evaluate_final_xi(assumption, confirmed)["status"] == "FINAL_CHECK_FAILED"


def test_price_gate_independent_of_lineup() -> None:
    assert evaluate_final_price(final_price=1.55, minimum_entry=1.70)["status"] == "PASS_PRICE"
    assert evaluate_final_price(final_price=1.75, minimum_entry=1.70)["status"] == "PRICE_OK"


def test_finalize_prioritizes_lineup_failure_over_price() -> None:
    assumption = LineupAssumption(assumed_available_player_ids=frozenset({"p1"}))
    confirmed = ConfirmedLineup(confirmed=True, confirmed_out_player_ids=frozenset({"p1"}))
    result = finalize_candidate(assumption, confirmed, final_price=2.0, minimum_entry=1.5)
    assert result["portfolio_status"] == "FINAL_CHECK_FAILED"
    assert result["portfolio_eligible"] is False


def test_finalize_still_gates_on_price_after_a_clean_xi_check() -> None:
    result = finalize_candidate(
        LineupAssumption(), ConfirmedLineup(confirmed=True), final_price=1.5, minimum_entry=1.7
    )
    assert result["portfolio_status"] == "PASS_PRICE"
    assert result["portfolio_eligible"] is False


def test_finalize_passes_when_both_gates_clear() -> None:
    result = finalize_candidate(
        LineupAssumption(), ConfirmedLineup(confirmed=True), final_price=1.9, minimum_entry=1.7
    )
    assert result["portfolio_status"] == "FINAL_CHECK_PASSED"
    assert result["portfolio_eligible"] is True
