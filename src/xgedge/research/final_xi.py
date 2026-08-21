"""Final XI + price gate: the last pre-kickoff check before portfolio entry.

Deep audit happens well before kickoff and assumes a lineup. This module
re-checks that assumption once official lineups are confirmed
(``WAITING_XI`` -> ``FINAL_CHECK_PASSED``/``FINAL_CHECK_FAILED``) and
separately gates on the actual tradeable price at that moment
(``PASS_PRICE``), independent of how positive the deep audit was. Only a
candidate that clears both gates is eligible for the portfolio engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MATERIAL_ROLES = frozenset({"goalkeeper", "centre_back", "main_striker"})


@dataclass(frozen=True, slots=True)
class LineupAssumption:
    """What the deep audit assumed would be available."""

    assumed_available_player_ids: frozenset[str] = frozenset()
    assumed_key_roles_available: frozenset[str] = frozenset()  # subset of MATERIAL_ROLES
    assumed_formation: str | None = None


@dataclass(frozen=True, slots=True)
class ConfirmedLineup:
    """Official pre-kickoff lineup facts."""

    confirmed: bool
    confirmed_out_player_ids: frozenset[str] = frozenset()
    missing_key_roles: frozenset[str] = frozenset()
    formation: str | None = None
    unexpected_rotation: bool = False


def evaluate_final_xi(assumption: LineupAssumption, confirmed: ConfirmedLineup) -> dict[str, Any]:
    """Compare the audited assumption against the officially confirmed lineup."""
    if not confirmed.confirmed:
        return {"status": "WAITING_XI", "material_change": False, "reasons": []}
    reasons: list[str] = []
    if assumption.assumed_available_player_ids & confirmed.confirmed_out_player_ids:
        reasons.append("assumed_available_player_confirmed_out")
    if (assumption.assumed_key_roles_available & MATERIAL_ROLES) & confirmed.missing_key_roles:
        reasons.append("material_role_missing")
    if confirmed.unexpected_rotation:
        reasons.append("unexpected_rotation")
    if (
        assumption.assumed_formation is not None
        and confirmed.formation is not None
        and assumption.assumed_formation != confirmed.formation
    ):
        reasons.append("formation_changed")
    material = bool(reasons)
    return {
        "status": "FINAL_CHECK_FAILED" if material else "FINAL_CHECK_PASSED",
        "material_change": material,
        "reasons": reasons,
        "requires_new_probability": material,
    }


def evaluate_final_price(*, final_price: float, minimum_entry: float) -> dict[str, Any]:
    """Gate on the actual tradeable price, independent of the deep-audit verdict."""
    price = float(final_price)
    minimum = float(minimum_entry)
    status = "PASS_PRICE" if price < minimum else "PRICE_OK"
    return {"status": status, "final_price": price, "minimum_entry": minimum}


def finalize_candidate(
    assumption: LineupAssumption,
    confirmed: ConfirmedLineup,
    *,
    final_price: float,
    minimum_entry: float,
) -> dict[str, Any]:
    """Combine the Final XI gate and price gate into one portfolio-entry verdict."""
    xi = evaluate_final_xi(assumption, confirmed)
    price = evaluate_final_price(final_price=final_price, minimum_entry=minimum_entry)
    if xi["status"] in {"WAITING_XI", "FINAL_CHECK_FAILED"}:
        portfolio_status = xi["status"]
    elif price["status"] == "PASS_PRICE":
        portfolio_status = "PASS_PRICE"
    else:
        portfolio_status = "FINAL_CHECK_PASSED"
    return {
        "schema_version": "final-xi-price-gate/1.0",
        "portfolio_status": portfolio_status,
        "final_xi": xi,
        "price_gate": price,
        "portfolio_eligible": portfolio_status == "FINAL_CHECK_PASSED",
    }
